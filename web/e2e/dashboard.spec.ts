import { mkdirSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

// End-to-end checks against the running dashboard and the REAL database (no fixtures, no mocks).
// E2E_REQUIRE_DATA=1 makes empty states a failure (use after a scored run exists).
const REQUIRE_DATA = process.env.E2E_REQUIRE_DATA === "1";
const PAGES = [
  { path: "/", heading: "Overview" },
  { path: "/shortlist", heading: "Shortlist" },
  { path: "/left-out", heading: "Left out" },
  { path: "/explore", heading: "Explore" },
  { path: "/runs", heading: "Runs" },
  { path: "/policy", heading: "AI Policy" },
  { path: "/methodology", heading: "Methodology", notInNav: true },
];
const DASHES = /[—–]/;
mkdirSync("test-results/screens", { recursive: true });

function watchConsole(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".animate-pulse.rounded-card")).toHaveCount(0); // skeletons replaced by data
}

async function noDashesNoOverflow(page: Page) {
  const text = await page.locator("body").innerText();
  const hit = text.match(new RegExp(`.{0,40}${DASHES.source}.{0,40}`));
  expect(hit, `em/en dash rendered: "${hit?.[0]}"`).toBeNull();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, "horizontal page scroll").toBeLessThanOrEqual(1);
}

test("basic auth is enforced", async ({ baseURL }) => {
  // Plain fetch: Playwright request contexts inherit the configured httpCredentials.
  expect((await fetch(`${baseURL}/`)).status).toBe(401);
  expect((await fetch(`${baseURL}/api/live`)).status).toBe(401);
  const bad = { Authorization: `Basic ${Buffer.from("upgrad:wrong").toString("base64")}` };
  expect((await fetch(`${baseURL}/`, { headers: bad })).status).toBe(401);
});

for (const p of PAGES) {
  test(`${p.heading}: loads real data, no console errors, no dashes, no overflow`, async ({ page }, info) => {
    const errors = watchConsole(page);
    const res = await page.goto(p.path);
    expect(res?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1, name: p.heading })).toBeVisible();
    await settle(page);
    if (REQUIRE_DATA && p.path !== "/") {
      await expect(page.getByRole("status").filter({ hasText: /^No /i })).toHaveCount(0);
    }
    await noDashesNoOverflow(page);
    await page.screenshot({ path: `test-results/screens/${info.project.name}${p.path === "/" ? "-overview" : p.path.replace(/\//g, "-")}.png`, fullPage: true });
    expect(errors, errors.join("\n")).toEqual([]);
  });
}

test("navigation: every nav link opens its page", async ({ page, isMobile }) => {
  await page.goto("/");
  for (const p of PAGES.slice(1).filter((x) => !("notInNav" in x))) {
    if (isMobile) await page.getByRole("button", { name: "Open menu" }).click();
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: p.heading, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${p.path}$`));
    await expect(page.getByRole("heading", { level: 1, name: p.heading })).toBeVisible();
  }
  await page.getByRole("link", { name: "Founder Screener home" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
});

test("overview: live KPIs, stage stepper and activity come from the database", async ({ page, request: api }) => {
  const live = await (await api.get("/api/live")).json();
  await page.goto("/");
  await settle(page);
  const kpis = page.getByTestId("kpi");
  await expect(kpis).toHaveCount(4);
  const values = await page.getByTestId("kpi-value").allInnerTexts();
  const companies = live.universe.filter((r: { area: string; bucket: string }) => r.area === "companies" && r.bucket === "total")
    .reduce((s: number, r: { count: number }) => s + r.count, 0);
  expect(values[1].replace(/,/g, "")).toBe(String(companies)); // KPI shows the real DB count
  if (live.active || live.latest) {
    await expect(page.getByRole("list", { name: "Pipeline stages" }).getByRole("listitem")).toHaveCount(7);
  }
  if (live.activity.length) await expect(page.getByTestId("activity-table")).toBeVisible();
  if (REQUIRE_DATA) {
    // Charts must draw bars and labels, not just axes.
    for (const id of ["funnel-chart", "fit-chart"]) {
      await expect(page.getByTestId(id).locator(".recharts-bar-rectangle path").first()).toBeVisible();
      await expect(page.getByTestId(id).locator(".recharts-label-list text").first()).toBeVisible();
    }
  }
});

test("run now: disabled while a run is active, and the API refuses a second run (409)", async ({ page, request: api }) => {
  const live = await (await api.get("/api/live")).json();
  await page.goto("/");
  const button = page.getByTestId("run-now");
  await expect(button).toBeVisible();
  if (live.active) {
    await expect(button).toBeDisabled();
    await expect(button).toHaveText(/Run in progress/);
    const res = await api.post("/api/runs", { data: { top_n: 5 } });
    expect(res.status()).toBe(409);
    expect((await res.json()).detail).toMatch(/already in progress/);
  } else {
    test.info().annotations.push({ type: "note", description: "No active run: the 409 path is covered when a run is active." });
    await expect(button).toBeEnabled();
  }
});

test("shortlist: filters, view toggle, CSV export and drill-down", async ({ page }) => {
  await page.goto("/shortlist");
  await settle(page);
  const cards = page.getByTestId("shortlist-card");
  const n = await cards.count();
  test.skip(n === 0 && !REQUIRE_DATA, "no published shortlist yet");
  expect(n).toBeGreaterThan(0);
  // Filter by each fit type, then reset.
  for (const fit of ["acquire", "partner", "co_build"]) {
    await page.getByTestId(`filter-${fit}`).click();
    const shown = await cards.count();
    const badges = await page.locator(`[data-testid=shortlist-card] [data-fit]`).evaluateAll((els) => els.map((e) => e.getAttribute("data-fit")));
    expect(badges.every((b) => b === fit)).toBe(true);
    expect(shown).toBeLessThanOrEqual(n);
    await page.getByTestId(`filter-${fit}`).click();
  }
  await expect(cards).toHaveCount(n);
  await page.getByTestId("view-table").click();
  await expect(page.getByTestId("shortlist-table")).toBeVisible();
  await page.getByTestId("view-cards").click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("export-csv").click()]);
  expect(download.suggestedFilename()).toMatch(/upgrad-shortlist-.*\.csv/);
  await cards.first().getByTestId("company-link").click();
  await expect(page).toHaveURL(/\/company\//);
  await expect(page.getByRole("heading", { name: "Score breakdown" })).toBeVisible();
});

test("company detail: claims link to sources and quotes are readable", async ({ page, isMobile }) => {
  await page.goto("/explore");
  await settle(page);
  const rows = page.getByTestId("explore-row");
  test.skip((await rows.count()) === 0 && !REQUIRE_DATA, "no scored companies yet");
  await rows.first().click();
  await expect(page).toHaveURL(/\/company\//);
  await settle(page);
  await expect(page.getByRole("heading", { name: "Score breakdown" })).toBeVisible();
  const table = page.getByTestId("claims-table");
  if (isMobile && (await page.getByTestId("claim-card").count())) {
    const card = page.getByTestId("claim-card").first();
    await expect(card.getByTestId("claim-quote")).toBeVisible(); // quote shown inline on touch screens
    await expect(card.getByRole("link")).toHaveAttribute("href", /^https?:\/\//);
    await page.getByTestId("claim-filter").selectOption({ index: 1 });
    await expect(page.getByTestId("claim-card").first()).toBeVisible();
  } else if (await table.count()) {
    const first = table.locator("tbody tr").first();
    await expect(first.getByRole("link")).toHaveAttribute("href", /^https?:\/\//);
    await first.getByTestId("quote").hover();
    await expect(page.getByTestId("quote-tip").first()).toBeVisible();
    await page.getByTestId("claim-filter").selectOption({ index: 1 });
    await expect(table.locator("tbody tr").first()).toBeVisible();
  }
  await noDashesNoOverflow(page);
});

test("left out: reason filter and sort", async ({ page }) => {
  await page.goto("/left-out");
  await settle(page);
  const table = page.getByTestId("left-out-table");
  test.skip((await table.count()) === 0 && !REQUIRE_DATA, "no exclusions yet");
  const all = await table.locator("tbody tr").count();
  const filter = page.getByTestId("reason-filter");
  const options = await filter.locator("option").count();
  if (options > 2) {
    await filter.selectOption({ index: 1 });
    expect(await table.locator("tbody tr").count()).toBeLessThanOrEqual(all);
  }
  await page.getByTestId("left-out-sort").selectOption("name");
  const names = await table.locator("tbody tr td:nth-child(2)").allInnerTexts();
  expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
  await filter.selectOption("all");
  if (await page.getByTestId("left-out-next").count()) {
    await page.getByTestId("left-out-next").click();
    await expect(page.getByText(/Page 2 of/)).toBeVisible();
  }
  await page.getByTestId("left-out-search").fill(names[0].slice(0, 4));
  await expect(table.locator("tbody tr").first()).toContainText(names[0].slice(0, 4), { ignoreCase: true });
});

test("explore: search, sort, filters and pagination", async ({ page }) => {
  await page.goto("/explore");
  await settle(page);
  const rows = page.getByTestId("explore-row");
  const n = await rows.count();
  test.skip(n === 0 && !REQUIRE_DATA, "no scored companies yet");
  const first = (await rows.first().locator("td").nth(1).innerText()).trim();
  await page.getByTestId("explore-search").fill(first.slice(0, 4));
  await expect(rows.first()).toContainText(first.slice(0, 4), { ignoreCase: true });
  await page.getByTestId("explore-search").fill("");
  await page.getByRole("button", { name: /Final/ }).click();
  await expect(page.locator("th[aria-sort=ascending], th[aria-sort=descending]").first()).toBeVisible();
  await page.getByTestId("explore-gate").selectOption("passed");
  await page.getByTestId("explore-gate").selectOption("all");
  await page.getByTestId("page-size").selectOption("50");
  if (await page.getByTestId("page-next").isEnabled()) {
    await page.getByTestId("page-next").click();
    await expect(page.getByText(/Page 2 of/)).toBeVisible();
  }
});

test("region filter: India first by default, Global shows every company", async ({ page }) => {
  const count = async (id: string) => Number(((await page.getByTestId(id).innerText()).match(/\((\d+)\)/) ?? [])[1]);
  for (const path of ["/shortlist", "/explore", "/left-out"]) {
    await page.goto(path);
    await settle(page);
    if (!(await page.getByTestId("region-india").count())) {
      test.skip(!REQUIRE_DATA, "no scored run yet");
      throw new Error(`${path}: region toggle missing`);
    }
    await expect(page.getByTestId("region-india")).toHaveAttribute("aria-current", "page");
    const india = await count("region-india");
    const global = await count("region-global");
    expect(india).toBeLessThanOrEqual(global);
    const regions = await page.locator("[data-region]").evaluateAll((els) => els.map((e) => e.getAttribute("data-region")));
    expect(regions.every((r) => r === "india"), `${path}: non-India row in the India view`).toBe(true);
    if (path === "/shortlist") await expect(page.getByTestId("shortlist-card")).toHaveCount(india);
    await page.getByTestId("region-global").click();
    await expect(page).toHaveURL(/region=global/);
    await expect(page.getByTestId("region-global")).toHaveAttribute("aria-current", "page");
    if (path === "/shortlist") await expect(page.getByTestId("shortlist-card")).toHaveCount(global);
    await noDashesNoOverflow(page);
  }
});

test("search and ask: type-ahead from the database, verified answer, then cited web research", async ({ page, request: api, isMobile }) => {
  test.setTimeout(150_000);
  // A real company from the latest shortlist (CSV column 3), so the test never depends on a hard-coded name.
  const csv = (await (await api.get("/api/shortlist.csv?region=global")).text()).split("\n");
  test.skip(csv.length < 2 && !REQUIRE_DATA, "no shortlist yet");
  const company = csv[1].split(",")[2].replace(/^"|"$/g, "");
  await page.goto("/");
  if (isMobile) await page.getByTestId("ask-open").click();
  else await page.keyboard.press("Control+k");
  await expect(page.getByTestId("ask-dialog")).toBeVisible();
  await expect(page.getByTestId("ask-mic")).toBeVisible();
  await page.getByTestId("ask-input").fill(company);
  await expect(page.getByTestId("ask-hit").first()).toContainText(company, { ignoreCase: true });
  await page.getByTestId("ask-input").press("Enter");
  await expect(page.getByTestId("ask-verified")).toBeVisible();          // database answer first (instant)
  await expect(page.getByTestId("ask-verified")).toContainText(company, { ignoreCase: true });
  await expect(page.getByTestId("ask-speak")).toBeEnabled();
  // Web research arrives later: either a cited answer or a clear message (never a silent failure).
  await expect(page.getByTestId("ask-web-text").or(page.getByTestId("ask-web-error"))).toBeVisible({ timeout: 90_000 });
  if (REQUIRE_DATA && (await page.getByTestId("ask-sources").count())) {
    await expect(page.getByTestId("ask-sources").getByRole("link").first()).toHaveAttribute("href", /^https?:\/\//);
  }
  await noDashesNoOverflow(page);
  if (await page.getByTestId("ask-web-text").count()) {
    // A repeat question is answered from the 24 h cache: no new Gemini call.
    await page.getByTestId("ask-submit").click();
    await expect(page.getByTestId("ask-cached")).toBeVisible({ timeout: 30_000 });
  }
  await page.getByTestId("ask-open-profile").click();
  await expect(page).toHaveURL(/\/company\//);
});

test("ask a question about the shortlist: answered from verified data, no web call", async ({ page, isMobile }) => {
  await page.goto("/");
  if (isMobile) await page.getByTestId("ask-open").click();
  else await page.keyboard.press("Control+k");
  await page.getByTestId("ask-input").fill("Tell me the best fit founder");
  await page.getByTestId("ask-submit").click();
  await expect(page.getByTestId("ask-insights")).toBeVisible();
  if (REQUIRE_DATA) await expect(page.getByTestId("ask-insight")).toHaveCount(3);
  await expect(page.getByTestId("ask-web")).toHaveCount(0);  // questions never spend Gemini
  await page.getByTestId("ask-input").fill("who should we co-build with?");
  await page.getByTestId("ask-submit").click();
  await expect(page.getByTestId("ask-insights")).toContainText(/co-build/i);
  await noDashesNoOverflow(page);
});

test("founder profiles, curated left out and clickable KPIs", async ({ page }) => {
  await page.goto("/shortlist?region=global");
  await settle(page);
  const cards = page.getByTestId("shortlist-card");
  test.skip((await cards.count()) === 0 && !REQUIRE_DATA, "no shortlist yet");
  const n = await cards.count();
  for (let i = 0; i < n; i++) await expect(cards.nth(i).getByTestId("founder-list")).toBeVisible();
  if (REQUIRE_DATA) {
    // Every shortlisted company names at least one founder, and most founders have a written line.
    await expect(page.getByTestId("founder")).not.toHaveCount(0);
    expect(await page.getByTestId("founder-line").count()).toBeGreaterThan(0);
  }
  await page.goto("/left-out");
  await settle(page);
  if (REQUIRE_DATA) {
    await expect(page.getByTestId("curated-card")).toHaveCount(3);
    for (const line of await page.getByTestId("curated-line").allInnerTexts()) expect(line.length).toBeGreaterThan(20);
  }
  await page.goto("/");
  await settle(page);
  await page.getByTestId("kpi").last().click();
  await expect(page).toHaveURL(/\/shortlist/);
});

test("AI policy: every section editable, unsaved changes tracked, impact preview from stored data", async ({ page, isMobile }) => {
  test.setTimeout(300_000);
  await page.goto("/policy");
  await expect(page.getByTestId("policy-bar")).toBeVisible({ timeout: 60_000 });
  for (const id of ["categories", "filters", "weights", "selection", "lists", "history"]) await expect(page.locator(`#${id}`)).toBeVisible();
  expect(await page.getByTestId("policy-category").count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByTestId("policy-changes")).toHaveText(/No changes/);
  // Edit: rename a category and add one, then discard.
  await page.getByTestId("category-label").first().fill("Acquire (renamed)");
  await page.getByTestId("policy-add-category").click();
  await expect(page.getByTestId("policy-category")).toHaveCount(4);
  await expect(page.getByTestId("policy-changes")).toHaveText(/unsaved change/);
  await expect(page.getByTestId("policy-diff")).toContainText("categories");
  await page.getByTestId("policy-discard").click();
  await expect(page.getByTestId("policy-changes")).toHaveText(/No changes/);
  if (isMobile) return;  // the preview recomputes every score: run it once (desktop)
  // Preview: India only. Every shortlisted company in the draft must then be India-located or unverified.
  await page.getByTestId("region-allowed-outside").click();
  await page.getByTestId("policy-preview").click();
  await expect(page.getByTestId("policy-preview-panel")).toBeVisible({ timeout: 240_000 });
  if (REQUIRE_DATA) {
    const left = await page.getByTestId("preview-left").locator("[data-region]").evaluateAll((els) => els.map((e) => e.getAttribute("data-region")));
    expect(left.every((r) => r === "outside")).toBe(true);
  }
  await noDashesNoOverflow(page);
});

test("runs and methodology show real run data", async ({ page }) => {
  await page.goto("/runs");
  await settle(page);
  await expect(page.getByTestId("run-card").first()).toBeVisible();
  await page.goto("/methodology");
  await settle(page);
  if (REQUIRE_DATA) await expect(page.getByText(/T = 0\.25 x prior exit/)).toBeVisible();
});

test("PDF export: report page mirrors the shortlist and the API returns a real PDF", async ({ page, request: api, isMobile }) => {
  test.skip(isMobile, "one PDF render is enough");
  await page.goto("/shortlist?region=global");
  await settle(page);
  const cards = page.getByTestId("shortlist-card");
  test.skip((await cards.count()) === 0 && !REQUIRE_DATA, "no shortlist yet");
  const n = await cards.count();
  await expect(page.getByTestId("export-pdf")).toHaveAttribute("href", /\/api\/shortlist\.pdf\?.*region=global/);
  await page.goto("/shortlist/report?region=global");
  await expect(page.getByTestId("report-cards").getByTestId("shortlist-card")).toHaveCount(n);
  const pdf = await api.get("/api/shortlist.pdf?region=global", { timeout: 90_000 });
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toBe("application/pdf");
  expect(pdf.headers()["content-disposition"]).toMatch(/upgrad-shortlist-global-.*\.pdf/);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
});

test("APIs return live JSON and CSV", async ({ request: api }) => {
  const live = await api.get("/api/live");
  expect(live.status()).toBe(200);
  const body = await live.json();
  expect(Object.keys(body).sort()).toEqual(["active", "activity", "at", "latest", "universe"]);
  const latest = await api.get("/api/runs/latest");
  expect(latest.status()).toBe(200);
  const csv = await api.get("/api/shortlist.csv");
  expect(csv.headers()["content-type"]).toContain("text/csv");
  expect((await csv.text()).split("\n")[0]).toContain("Rank,Founder(s),Company");
});
