# Frontend spec (`/web`)

Page-by-page spec for the dashboard. Read with [ARCHITECTURE.md](ARCHITECTURE.md) (data contract,
control flow) and [BRAND.md](BRAND.md) (tokens). Stack: Next.js 16 App Router, TypeScript strict,
Tailwind v4, shadcn/ui, TanStack Table, Recharts, SWR, zod, postgres.js (server-only).

## Global rules

- **Data access:** server components read `v_*` views through `web/lib/db.ts` (server-only, `web_ro`)
  and validate rows with zod (`web/lib/schemas.ts`). Never query a raw table. Never import `db.ts` from a
  client component.
- **Client components** only for interactivity: filters, view toggles, polling, charts, table state.
- **Run selection:** every page defaults to the latest published run (`is_latest`). A `?run=<run_id>`
  query param selects another run; a banner shows "Viewing run of <date>" with a link back to latest.
- **Auth:** `proxy.ts` enforces HTTP basic auth (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`) on every
  route, including `/api/*`. Missing env vars → deny all (fail closed).
- **Caching:** `export const revalidate = 60` on data pages; run status polled by SWR (§6 of ARCHITECTURE).
- **States:** every data region has a skeleton loader, an empty state and an error state with a retry
  action. Missing or null fields render as "–" and never throw.
- **Responsive** down to 375px: card layouts stack; tables scroll inside their own container; no
  horizontal page scroll.
- **Accessibility:** keyboard-reachable everything, visible focus ring (`--brand`), semantic landmarks,
  `aria-live="polite"` on the run stepper, colour never the only cue (badges carry text), WCAG AA per BRAND §2.2.
- **Numbers:** all scores 0–1 shown to 2 decimals with `tabular-nums`; money in USD with compact
  notation (`$4.2M`); dates as `24 Oct 2026`.
- **Theme:** light first. Dark optional (BRAND §3).
- **Layout shell:** top bar with wordmark ("Founder Screener" / "for upGrad"), nav (Overview, Shortlist,
  Left out, Explore, Runs, Methodology), run status pill, "Run now" button. Mobile: nav collapses into a sheet.

## Shared components

| Component | Purpose |
|---|---|
| `RunStatusPill` | `queued` / `running` / `succeeded` / `partial` / `failed` with icon and semantic colour |
| `RunNowButton` | `POST /api/runs`; disabled while a run is `queued`/`running`; handles 409 (toast: "A run is already in progress") and 502 |
| `StageStepper` | Discover → Resolve → Enrich → Verify → Score → Explain → Publish; done / active (progress bar) / pending / failed |
| `FitBadge` | Acquire / Partner / Co-build using fit tokens, label always visible |
| `ReasonBadge` | Exclusion reason code with human label and tooltip |
| `ScoreBar` | Horizontal 0–1 bar (`--brand` fill) with numeric value; used for T, I, F, C, Final |
| `EvidenceLink` | Link showing the source domain, opens in a new tab with `rel="noopener noreferrer"` |
| `EmptyState`, `ErrorState`, `Skeleton*` | Standard states |

---

## 1. Overview (`/`)

Reads: `v_runs` (latest + active), `v_funnel`, `v_shortlist` (fit split).

- Header: wordmark, `RunStatusPill` for the latest run, `RunNowButton`.
- While a run is active: `StageStepper` live (SWR poll 5s), current stage progress, elapsed time.
- KPI cards (from `v_runs.counts` of the latest published run): **Candidates found**, **Companies
  resolved**, **Claims verified** (`claims_supported`), **Shortlisted**. Each card shows the delta vs
  the previous published run if one exists.
- Funnel chart (Recharts horizontal bars) from `v_funnel`: discovered → resolved → enriched → verified →
  gated → scored → shortlisted, with counts and drop-off %.
- Fit-type split: count of shortlisted entities by fit type (bars in fit colours, labelled).
- "Open Google Sheet" button (`GOOGLE_SHEET_URL`; hidden if unset).
- Empty state (no runs yet): "No runs yet. Start the first run to build a shortlist." + Run now.

## 2. Shortlist (`/shortlist`)

Reads: `v_shortlist`.

- Ranked cards, one per entity:
  - Rank, company name + website link, `FitBadge`.
  - Founder name(s), each linked to its LinkedIn URL (if present) with role.
  - `ScoreBar`s for T, I, F, C and the Final score (prominent).
  - "Why they made the cut" (`why_line`) and "How they fit upGrad" (`fit_line`).
  - Up to 3 `EvidenceLink`s showing their domains.
  - "Last verified" date; reviewer label badge if present.
  - Card links to `/company/[entityId]`.
- Filters (client, URL-synced): fit type (multi-select), minimum confidence (slider 0–1).
- Toggle: card view / table view (table uses the same columns as the Sheet's Shortlist tab).
- "Export CSV" → `GET /api/shortlist.csv?run=<id>` (filters applied, Sheet column order).
- "Export PDF" → `GET /api/shortlist.pdf` (same filters): headless Chromium (`playwright-core`) renders
  `/shortlist/report` to A4 with page numbers and clickable sources: a cover summary, two cards per page (company and
  founders beside scores and reasons) and the curated left-out picks. Without a browser on the host it redirects to
  the report page, which opens the browser's own Save as PDF dialog.
- Both exports use `ExportButton` (fetch + save, spinner and disabled while working, error message on failure).
  The PDF renders on one warm shared Chromium page (`lib/pdf-renderer.ts`, ~1.5 s; closes after 10 idle minutes);
  the Shortlist page pre-starts it with `GET /api/shortlist.pdf?warm=1` because a cold start takes ~15 s.
- Empty state: "No shortlist for this run." Filtered-to-zero state: "No entries match these filters." + reset.

## 3. Company detail (`/company/[entityId]`)

Reads: `v_company_detail` (by `entity_id` + run).

- Profile: name, website, location, founded year, status, total raised, headcount, fit type, rank (or
  "Not shortlisted" with link to its Left out entry).
- Founders: name, role, current/past, LinkedIn link.
- Funding timeline: rounds in date order (type, amount, investors, source link). Empty: "No verified rounds."
- Score breakdown: each sub-score (T, M, N, I, F per lens, C, Final, BT, Final′) with raw → adjusted
  value, number of verified signals (`n_signals`), pool mean, and weight/exponent. The weakest of T/I/F
  shows its `why_not_higher` note.
- Claims table: field, value, verifier confidence, retrieved date, source link. Hover/focus on a row
  shows the `evidence_span` quote in a tooltip (also reachable by keyboard; on touch, tap to expand).
- 404 state if the entity is not in the selected run.

## 4. Left out (`/left-out`)

Reads: `v_left_out`.

- List/table: company, founders, `ReasonBadge`, explanation, would-have-ranked (e.g. "#7" or "–"),
  source link.
- Filter by reason code; sort by would-have-ranked.
- Reason labels: TOO_LARGE "Too large", COMPETITOR "Competitor at scale", UPGRAD_OWNED "Already upGrad",
  UNVERIFIABLE "Not verifiable", INACTIVE "Inactive", LOW_FIT "Low fit", NOT_EDUCATION "Not education",
  DUPLICATE_THESIS "Duplicate thesis".

## 5. Explore (`/explore`)

Reads: `v_long_list` (top 200 of the selected run).

- TanStack Table: sortable columns (rank, company, fit type, final, t, m, n, i, f, per-lens F, confidence,
  gate status); global search (company, founders); column filters (fit type, gate status, numeric ranges);
  column visibility; pagination (25/50/100). Row click → company detail.
- Table state synced to the URL so views are shareable.

## 6. Runs (`/runs`)

Reads: `v_runs`.

- Table of runs, newest first: started, duration, status pill, trigger, counts, cost (USD), provider
  quota usage (per provider: calls, errors, quota used/limit as a mini bar), error summary (expandable).
- Selecting a run sets `?run=` and links to its Overview/Shortlist.
- Active run row shows the live `StageStepper`.

## 7. Methodology (`/methodology`)

Reads: `v_runs.config_snapshot` of the selected run. Static content from SPEC.md.

For the judges: makes every ranking explainable.
- Definitions: track-record signals, "interesting", fit lenses (from SPEC §1).
- Scoring formulas (rendered as formatted text, not images) with the **actual weights and constants
  from that run's `config_snapshot`**, not hard-coded copies.
- Gap map used in that run: business units and wanted capabilities.
- Exclusions and size ceiling used in that run.
- Verification rules: Claim fields, the Shortlist gate (identity, role, company existence + ≥3 verified claims).
- LinkedIn policy (search snippets only, never scraped, never logged in) and data sources list.
- Calibration report if present: recall@50 and the weight-perturbation Kendall τ.

## 7a. AI Policy (`/policy`)

Reads: `screener policy show` (via `lib/policy-cli.ts`, local mode only) and `v_policy`. Writes only through the
backend CLI; the dashboard's DB role stays read-only.
- Structured form (no free text policy): categories (add, rename, define, enable: id, name, description, required
  capabilities/segments, raised range, unknown-size factor, stage factors, founder boost, geo), 20 filters
  (education, inactive, acquired, competitors, upGrad-owned, size ceilings, identity/role/existence gates,
  min track-record claims, regions, founded window, funding, stages, confidence, min Final, min verified claims),
  score weights, selection (shortlist size, MMR lambda, min India), blocklist / gap-map lists.
- **Preview**: `POST /api/policy/preview` shows entered / left / moved, by category, by region and reasons,
  computed without LLM calls. **Save** writes and activates a new version; **Save & re-score** also starts
  `screener rescore`. Version history lists every version with its diff; **Activate** rolls back.
- Category names come from the active version (`/api/policy/labels` on the client via `useFitLabels`, `lib/fit-labels.ts`
  on the server): badges, fit filters (Shortlist, Explore), the fit chart, the CSV and the company score breakdown.
  Filters list the enabled categories plus any fit type present in the run. Written why/fit lines belong to their
  run and change on re-score. Saving or activating a version takes effect on these names immediately.

## 8. Search and ask (header, every page)
- Header button "Search or ask" (also Ctrl+K and "/"); the Overview has an "Ask about a founder or company" button.
- Type-ahead over `v_search`: companies, founders and the MCA/DPIIT registry (~1.46 lakh rows), exact and prefix
  matches first. Enter asks about the highlighted match; Alt+Enter asks about the exact text; clicking a company opens it.
- Answer, in two phases: (1) instantly, what the screener has **verified** (green card: description, founders with
  lines and background, verified raised amount, corroborated rounds with sources) and the MCA registry entry (CIN,
  status, registration date, paid-up and authorised capital); (2) after ~20-30 s, **web research** by Gemini with
  Google Search grounding, labelled "Not verified by the screener", with numbered citations linked to the sources.
- Voice: the mic uses the browser's speech recognition (Chrome / Edge, en-IN) and asks when you stop speaking;
  "Read aloud" uses the browser's speech synthesis. No audio leaves the browser except to the browser's own service.
- Cache: a web answer is saved on the dashboard server (repo `.cache/ask/`, `ASK_CACHE_HOURS`, default 24) keyed by
  the question and the matched entity, so a repeat question never spends Gemini again; the answer is marked "Saved
  answer from Xh ago" and a Refresh button forces new research. Only sourced answers are cached, never errors.
- API: `GET /api/search?q=` (type-ahead), `POST /api/ask {q, entity_id?, web?, refresh?}`. Gemini key and model names live
  in `web/.env.local` (`GEMINI_API_KEY`, `GEMINI_MODEL_FAST`); without them the web part says so.

## 9. Interaction
- Buttons lift on hover, sink on press, show a focus ring for keyboard users, and show a spinner while working;
  cards that open something (shortlist, KPIs, runs, curated left out) lift on hover. All motion is disabled when
  the OS asks for reduced motion.
- Founders on Shortlist and Company pages: title, identity-verified tick, LinkedIn / X links, a one-line bio from
  verified claims, and background chips (exits, roles, education) that open their source.
- Left out starts with the three curated picks and their cited reasons, then the full filterable list.
