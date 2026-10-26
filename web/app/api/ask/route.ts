import { connection } from "next/server";

import { askWeb } from "@/lib/ask";
import { type Intent, nameFrom, parseIntent } from "@/lib/intent";
import { getCompany, getShortlist, searchEntities, shortlistFunding } from "@/lib/queries";
import type { CompanyDetail, SearchHit, Shortlist } from "@/lib/schemas";

type InsightItem = {
  rank: number; company: string; entity_id: string; fit_type: string | null; region: string; line: string;
  detail?: string; founders: { name: string; title?: string | null; line?: string | null }[];
};

const usdShort = (v: number) => (v >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${Math.round(v / 1e3)}K`);

/** Answer a question from the latest verified shortlist (no Gemini, no web). */
async function insights(intent: Intent): Promise<{ title: string; items: InsightItem[]; note: string }> {
  const rows = (await getShortlist()).filter((r: Shortlist) => (!intent.fit || r.fit_type === intent.fit)
    && (!intent.region || (intent.region === "india" ? r.region === "india" : r.region !== "india")));
  const item = (r: Shortlist, detail?: string): InsightItem => ({
    rank: r.rank, company: r.company, entity_id: r.entity_id, fit_type: r.fit_type, region: r.region, line: r.why_line,
    detail, founders: r.founders.map((f) => ({ name: f.name, title: f.title ?? f.role, line: f.line })),
  });
  const note = "From the screener's latest verified shortlist: ranked by score after the pairwise judge, every fact sourced.";
  if (intent.kind === "exits") {
    const items = rows.flatMap((r) => r.founders.filter((f) => f.background.some((b) => b.field === "prior_exit")).map((f) => {
      const exit = f.background.find((b) => b.field === "prior_exit")!.value as Record<string, string | null>;
      return item(r, `${f.name}: exit from ${exit.company}${exit.acquirer ? ` (acquired by ${exit.acquirer})` : ""}`);
    }));
    return { title: intent.title, items, note: items.length ? note : "No shortlisted founder has a verified prior exit yet." };
  }
  if (intent.kind === "raised") {
    const funding = new Map((await shortlistFunding()).map((h) => [h.entity_id, h.total_raised_usd]));
    const items = rows.filter((r) => funding.get(r.entity_id)).sort((a, b) => (funding.get(b.entity_id) ?? 0) - (funding.get(a.entity_id) ?? 0))
      .slice(0, intent.limit).map((r) => item(r, `Verified raised: ${usdShort(funding.get(r.entity_id)!)}`));
    return { title: intent.title, items, note: `${note} Only corroborated funding counts.` };
  }
  return { title: intent.title, items: rows.slice(0, intent.limit).map((r) => item(r)), note };
}

const MONEY_FIELDS = new Set(["funding_round", "total_funding", "revenue", "acquired"]);

/** What the screener has verified about a company: facts with their sources, no web guesses. */
function verified(c: CompanyDetail) {
  return {
    entity_id: c.entity_id, company: c.company, website: c.website, description: c.description,
    location: [c.city, c.country].filter(Boolean).join(", ") || null, region: c.region, status: c.status,
    founded_year: c.founded_year, total_raised_usd: c.total_raised_usd, headcount: c.headcount, fit_type: c.fit_type,
    shortlist_rank: c.rank, rounds: c.rounds,
    founders: c.founders.map((f) => ({ name: f.name, title: f.title ?? f.role, line: f.line, linkedin_url: f.linkedin_url,
      x_url: f.x_url, identity_verified: f.identity_verified, background: f.background })),
    financial_claims: c.claims.filter((x) => MONEY_FIELDS.has(x.field)).slice(0, 8),
    claims_count: c.claims.length,
  };
}

// "Ask": database matches + verified facts (instant) and Gemini web research with Google Search (sources cited).
export async function POST(request: Request) {
  await connection();
  const body = (await request.json().catch(() => ({}))) as { q?: string; entity_id?: string; web?: boolean; refresh?: boolean };
  const q = (body.q ?? "").trim().slice(0, 200);
  if (q.length < 2) return Response.json({ detail: "Type at least 2 characters." }, { status: 400 });
  const name = nameFrom(q);
  // A question about the screener's own results ("best fit founder", "top 5 to acquire in India") is answered from
  // the verified shortlist, unless it names a company or founder we know.
  const intent = parseIntent(q);
  if (intent && !body.entity_id) {
    const named = name.length >= 3 ? (await searchEntities(name, 3).catch(() => [])).filter((m) => m.kind !== "registry") : [];
    if (!named.length) {
      try {
        return Response.json({ q, intent: intent.kind, insights: await insights(intent), matches: [], target: null,
          verified: null, registry: null, web: null, web_error: null }, { headers: { "Cache-Control": "no-store" } });
      } catch (err) {
        console.error("[api/ask] insights", err);
      }
    }
  }
  let matches: SearchHit[] = [];
  let target: SearchHit | undefined;
  let detail: CompanyDetail | null = null;
  try {
    matches = await searchEntities(name || q, 8);
    target = matches.find((m) => m.company_id && (m.company_id === body.entity_id || m.entity_id === body.entity_id))
      ?? matches.find((m) => m.kind !== "registry");
    if (target?.company_id) detail = await getCompany(target.company_id);
  } catch (err) {
    console.error("[api/ask] db", err);
  }
  const registry = matches.find((m) => m.kind === "registry" && (!target || m.name.toLowerCase().includes(target.name.toLowerCase().split(" ")[0])))
    ?? (target ? undefined : matches.find((m) => m.kind === "registry"));
  const hint = target ? [target.kind === "founder" ? `${target.name}, founder of ${target.company}` : target.name, target.website]
    .filter(Boolean).join(", ") : registry ? `${registry.name} (CIN ${registry.cin})` : undefined;
  let web = null;
  let webError: string | null = null;
  if (body.web !== false) {
    try {
      web = await askWeb(name || q, hint, { refresh: Boolean(body.refresh) });
      if (!web) webError = "Web research is off: GEMINI_API_KEY / GEMINI_MODEL_FAST are not set in web/.env.local.";
    } catch (err) {
      console.error("[api/ask] gemini", err);
      const msg = err instanceof Error ? err.message : String(err);
      webError = msg === "timeout" ? "Web research timed out."
        : /spend cap/i.test(msg) ? "Web research is paused: the Gemini project's spend cap has been reached. Raise it in Google AI Studio or Cloud Billing to turn web research back on."
          : /quota|RESOURCE_EXHAUSTED|429/i.test(msg) ? "Web research is busy (Gemini rate limit). Try again in a minute."
            : "Web research failed.";
    }
  }
  return Response.json(
    { q, matches, target: target ?? null, verified: detail ? verified(detail) : null, registry: registry ?? null, web, web_error: webError },
    { headers: { "Cache-Control": "no-store" } },
  );
}
