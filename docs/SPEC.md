# upGrad Founder Screener: Specification

Status: v0.2 (pre-implementation). Owner: lead engineer. Engineering rules: [../CLAUDE.md](../CLAUDE.md).
How the pipeline, Postgres, Google Sheet, GitHub Actions and dashboard connect, including the `v_*`
views the dashboard reads: [ARCHITECTURE.md](ARCHITECTURE.md).

## 0. Purpose and output

Discover education founders and companies (India-first), verify every fact against a source URL,
score them with explicit, code-computed criteria, and publish a live Google Sheet:

- **Shortlist**: top 10–15 founders/companies, each with reasons, fit type and evidence links.
- **Left out**: strong candidates that did not make it, each with a reason code.

Audience: upGrad's partnerships / M&A / co-build team. The pipeline runs end to end on real data only.

---

## 1. Definitions (all checkable)

A signal counts only when it is backed by at least one Claim with `verified_status = supported`.

### 1.1 Track record signals (founder-level)

| Signal | Feature key | Checkable definition |
|---|---|---|
| Prior exit | `prior_exit` | Founder co-founded a company that was acquired or IPO'd. Evidence: acquisition/IPO announcement, filing or reputable press naming the founder's company. Binary. |
| Prior VC-backed company | `prior_vc_backed` | Founder co-founded an earlier company that raised an institutional (VC) round. Evidence: round announcement, investor portfolio page, or funding database page. Binary. |
| Top investor | `top_investor` | Current company is backed by an investor on the tier-1 list or edtech-specialist list (maintained in `config/sources.yaml` → `vc_portfolios`). Evidence: investor portfolio page or round announcement. Binary. |
| Revenue / scale evidence | `scale` | Revenue, ARR, learners, paying users, or institutions served, as a number with a date. Money is log-transformed before percentile ranking. Continuous. |
| Years in education | `years_in_edu` | Sum of years the founder has worked in education companies or roles, from dated role claims. Continuous. |
| Operator pedigree | `operator_pedigree` | Founder held a role at manager level or above at a scaled edtech (≥$50M raised or ≥500 employees at the time). Binary. |
| Technical depth | `technical_depth` | Verifiable GitHub activity, peer-reviewed papers, or granted/filed patents attributed to the founder. Feeds `accelerator_or_award` slot only if no recognition exists; otherwise reported as supporting evidence. Binary. |
| Selective accelerator | `accelerator` | Company or founder went through an accelerator with a published selection rate (e.g. YC, Techstars, Antler, Sequoia Surge/Peak XV Surge; list in `config/sources.yaml` → `accelerators`). Binary. |
| Recognition with a selection bar | `award` | Award or list with a selection process (e.g. Forbes 30 Under 30, national awards). Self-nominated or pay-to-list recognitions do not count. Binary. |

`accelerator_or_award = max(accelerator, award, technical_depth)`.

### 1.2 Interesting (company-level)

**Interesting = momentum + novelty + defensibility + not already a giant.**

- **Momentum (M)**: funding velocity (rounds per year, amount growth), hiring growth (job-post counts
  over time), product launches, GitHub stars / Product Hunt traction, recency of activity.
- **Novelty (N)**: distance of the company's embedding from crowded embedding clusters, multiplied by
  the growth of its theme (cluster funding growth).
- **Defensibility**: proprietary data, regulatory approvals, institutional contracts, or technical IP,
  each a verified claim. Reported as evidence and used as a tie-break in explanations; not a separate
  sub-score in v0.1.
- **Not already a giant**: enforced through the size ceiling in `config/exclusions.yaml`
  (max total raised $100M, max headcount 1,000). Above the ceiling → excluded as `TOO_LARGE`.

### 1.3 Fit lenses

Computed against `config/upgrad_gap_map.yaml`:

- **Acquire**: capability fills a wanted capability; size within the ceiling; India presence.
- **Partner**: complements an existing business unit (distribution, content, tech integration).
- **Co-build**: early stage; strong founder track record; capability in a wanted area with no current upGrad offering.

`fit_type = argmax` over the three lenses.

---

## 2. Data model

Postgres 17 with pgvector, run as the `pgvector/pgvector` Docker image (locally now, on a hosted
machine later). Schema and views are managed by Alembic. All tables have `created_at` and `updated_at`.
IDs are UUIDs unless noted. Embeddings are pgvector `vector` columns without a fixed dimension, with the
producing model in `embedding_model` (Gemini and the fastembed fallback differ in size; vectors are only
ever compared within one model).

| Table | Purpose | Key fields |
|---|---|---|
| `companies` | Canonical companies | id, name, normalized_name, website, domain, country, city, founded_year, status (active/inactive/unknown), total_raised_usd, headcount, description, embedding, embedding_model |
| `founders` | Canonical people | id, full_name, normalized_name, linkedin_url, other_urls_json, current_title |
| `company_founders` | Founder ↔ company | company_id, founder_id, role, is_current, start_date, end_date |
| `rounds` | Funding rounds | id, company_id, round_type, amount_usd, announced_on, investors_json, source_claim_id |
| `claims` | Every fact (see below) | |
| `candidates` | Entities in a run's pool | id, run_id, company_id, founder_ids_json, stage (discovered/resolved/enriched/verified/scored), gate_status |
| `scores` | Code-computed scores | candidate_id, run_id, T, M, N, I, F, F_acquire, F_partner, F_co_build, fit_type, C, final, bt_score, bt_pct, final_prime, rank (by Final′), shortlist_rank (MMR order; null = not shortlisted), features_json, breakdown_json (per sub-score raw/adjusted/n_signals/pool_mean/weight/why_not_higher), evidence_json (top 3 {url,title,domain}), why_line, fit_line, weights_version |
| `exclusions` | Why a candidate was left out | candidate_id, run_id, reason_code, explanation, would_have_ranked (rank by Final′ ignoring the gate; null if never scored), source_claim_id, evidence_claim_ids_json |
| `feedback` | Reviewer input from the Sheet/dashboard | id, entity_id, reviewer, label, note, synced_at |
| `runs` | One pipeline execution | id, trigger (cli/dashboard/schedule), status, current_stage, stage_progress, started_at, finished_at, heartbeat_at, config_snapshot (weights, gap map, exclusions, model names), config_hash, git_sha, cost_usd, provider_usage_json, counts_json, sheet_url, error_summary |
| `run_funnel` | Stage counts per run | run_id, stage, stage_order, count |
| `api_calls` | Every external call | id, run_id, provider, endpoint, status, http_status, latency_ms, tokens_in, tokens_out, cost_usd, cache_hit, error |
| `discoveries` | Raw scout output before entity resolution (scouts cast a wide net; quality is decided later) | id, batch_id (one per `screener discover`), scout, raw_company_name, raw_person_name, company_url, person_url (LinkedIn URLs stored, never fetched), source_url, snippet, evidence_span (LLM-extracted items only kept if the span is found in the source with rapidfuzz partial_ratio >= 90 and contains the extracted name), extra_json, dedupe_key (unique), discovered_at, company_id / founder_id (set by `screener resolve`), resolved_at |
| `company_aliases` | Every raw name a company was seen under | company_id, alias, normalized (unique per company) |
| `resolution_pairs` | Audit of pairwise merge decisions; LLM tiebreaks are reused on re-runs | pair_key, left_name, right_name, decision (merge/separate), method (domain / rule:name+founder / rule:name+description / llm / domain_conflict / domain_guard), reason, features_json |
| `provider_quota` | Usage counters that keep free tiers from being exceeded | provider, period (`YYYY-MM` monthly / `YYYY-MM-DD` daily), used, limit; limits from `config/providers.yaml` |

### 2.1 Claim

| Field | Type | Notes |
|---|---|---|
| `id` | uuid | |
| `entity_type` | enum | `company` \| `founder` \| `round` |
| `entity_id` | uuid | |
| `field` | str | e.g. `current_role`, `prior_exit`, `total_raised_usd` |
| `value_json` | json | Typed value; `null` if unknown. Never fabricated. |
| `source_url` | str | Required. No source = no claim. |
| `source_type` | enum | `company_site` \| `press` \| `filing` \| `investor_site` \| `search_snippet` \| `github` \| `paper` \| `patent` \| `other` |
| `retrieved_at` | datetime | UTC |
| `evidence_span` | text | Exact text from the source supporting the value |
| `extractor_model` | str | Model name from config |
| `extract_confidence` | float 0–1 | From the extractor |
| `verified_status` | enum | `unverified` \| `supported` \| `contradicted` \| `not_found` |
| `verifier_confidence` | float 0–1 | From the verifier |

### 2.1a Claim lifecycle (enrich -> verify)
- **Fields.** company: description, city, country, founded_year, headcount, legal_name, incorporation_date,
  director, segment, capability (gap-map ids), open_roles, funding_round, revenue, acquired, latest_news_date,
  github_stars, github_last_push, website. founder: current_role, prior_role, prior_exit, education.
- **Extraction** (`screener enrich`): every prompt says "Return null if the text does not state it. Copy
  evidence_span verbatim." A fact is stored only if its span passes the grounding check: rapidfuzz
  partial_ratio >= 90 against the fetched text AND every number in the span appears exactly in the source.
  `claim_key` (entity, field, value, source) makes re-runs idempotent. Money keeps the original amount/unit/
  currency and adds `amount_usd` at `config/enrich.yaml` `inr_per_usd`. A page's publication date comes from its
  HTML metadata and is included in the page text as "Published: YYYY-MM-DD (page metadata)".
- **Never fetched:** linkedin.com, crunchbase.com, tracxn.com (search snippets only; verified against the stored
  snippet). Tofler/Zauba: one polite fetch each per company. PDL: top 100 by preliminary priority, 100/month.
- **Verification** (`screener verify`): (1) grounding re-check on a fresh fetch; (2) a separate Gemini-fast
  verifier sees only plain-language claim statements + the page text and returns supported / contradicted /
  not_found + confidence; (3) funding_round, revenue, acquired, prior_exit need 2 independent domains or 1 primary
  source (company site, acquirer's site, government domain) and only count when `meta.corroboration.corroborated`;
  (4) liveness (v0.4): INACTIVE only on combined evidence: the website does not resolve (after trying the
  www/apex host and http) AND no dated activity within 12 months, or a founder is no longer listed AND the latest
  activity is older than 12 months. ACTIVE if the site resolves and there is activity within 12 months (news,
  GitHub push, funding round, or open roles seen on the company's site); else UNKNOWN (lowers confidence only);
  (5) founder `identity_verified` = a supported non-LinkedIn current_role (or registry director) claim links the
  founder to the company. Corroborated rounds become `rounds` rows; supported facts are materialized on
  `companies`/`founders` (claims stay the source of truth).

### 2.2 Runs: lifecycle and stages

Statuses: `queued` → `running` → `succeeded` | `partial` | `failed`.
- `queued`: row inserted at process start, before the advisory lock and preflight checks.
- `running`: lock held, preflight passed (DB, config, at least one search provider, Gemini key).
- `partial`: Publish completed but coverage was reduced (budget guard stopped a stage, or the Sheet write failed after retries).
- `failed`: preflight failed, unrecoverable error, or stale heartbeat (> 15 min).

Stages, in order: **discover → resolve → enrich → verify → score → explain → publish**. At each stage
boundary, and at least every 30 s inside a stage, the pipeline checkpoints `current_stage`,
`stage_progress` (0–1), `counts_json`, `provider_usage_json` and `heartbeat_at`.

Funnel stages (`run_funnel`): discovered → resolved → enriched → verified → gated → scored → shortlisted.

### 2.3 Dashboard contract
The dashboard reads only the `v_*` views (`v_shortlist`, `v_company_detail`, `v_left_out`,
`v_long_list`, `v_runs`, `v_funnel`), never these tables. Row shapes are defined in ARCHITECTURE.md §4
and `src/screener/contracts.py`.

---

## 3. Scoring math

All weights and constants live in `config/weights.yaml`. The LLM never produces a score.

### 3.1 Normalization
- Each feature is normalized by **percentile rank within the current pool** → [0, 1].
- Money features are **log-transformed** (`log1p`) before ranking.
- `pct(x)` below means percentile rank within the pool.

### 3.2 Track record T (founder-level, rolled up to company)

```
T_founder = 0.25·prior_exit + 0.20·prior_vc_backed + 0.15·top_investor
          + 0.15·pct(log scale) + 0.10·pct(years_in_edu)
          + 0.10·operator_pedigree + 0.05·accelerator_or_award

T = min(1, max over co-founders(T_founder) + 0.05·team_depth_bonus)
```
`team_depth_bonus = 1` when at least two co-founders each have ≥1 verified track-record signal.

### 3.3 Interesting I
- **M** = mean of available momentum percentiles, each weighted by recency decay
  `w = 0.5^(age_days / 180)` (half-life 180 days). Missing features are skipped, not zero-filled.
- **N** = `pct(distance to crowded cluster centroids) × pct(cluster funding growth over the last 8 quarters)`.
- **I** = `0.5·M + 0.5·N`.

### 3.4 Fit F
- `F_lens` = capability overlap/complementarity vs the gap map + size/geo constraints, for each of Acquire, Partner, Co-build.
- `F = max over lenses`; `fit_type = argmax`.

### 3.5 Shrinkage (small-evidence correction)
For each sub-score s ∈ {T, I, F}:
```
s_adj = (n·s_raw + k·μ_pool) / (n + k),   k = 3
```
`n` = number of verified signals for that sub-score; `μ_pool` = pool mean of that sub-score.

### 3.6 Confidence C
`C = min(1, verified_claims / expected_claims)`. `expected_claims` is defined per entity type in `config/weights.yaml`.

### 3.7 Gates (all must pass, else excluded)
- Education-relevant (else `NOT_EDUCATION`)
- Active (else `INACTIVE`)
- Verified identity (else `UNVERIFIABLE`)
- Not in exclusions (`COMPETITOR`, `UPGRAD_OWNED`, `TOO_LARGE`)
- Shortlist only: identity, current role and company existence verified, plus ≥3 verified track-record/interest claims (else `UNVERIFIABLE`).

### 3.8 Final score
```
Final = T^0.30 · I^0.30 · F^0.30 · C^0.10 · region_multiplier
```
Each factor is floored at ε = 0.01 before exponentiation. India first (v0.4): `region_multiplier` = 1.0 for
companies located in India, 0.85 when the location is unverified, 0.6 outside India (`weights.yaml final`).

### 3.9 Pairwise judge → Bradley–Terry
- Take the **top 60** by Final.
- LLM judges pairs (which candidate is the stronger fit for upGrad, with reasons). **Each pair is judged in both orders** to cancel position bias; disagreements count as ties.
- Fit a Bradley–Terry model with `choix` → `bt_score`.
- `Final' = 0.7·Final + 0.3·pct(BT)`.

### 3.10 Shortlist selection by MMR
```
next = argmax_x [ λ·Final'(x) − (1−λ)·max_sim(x, selected) ],   λ = 0.75
sim  = embedding cosine (+0.1 if same fit_type)
```
Select until 10–15 entries. Candidates dropped for redundancy get `DUPLICATE_THESIS`. India first (v0.4): at
least 10 India-located companies when the eligible pool has them (`mmr.min_india`).

### 3.11 Calibration
- Seed positives/negatives in `config/seeds.yaml`.
- Report **recall@50** of seed positives (and how many seed negatives land in the top 50).
- **Stability check**: perturb each weight by ±20% (random draws), recompute, report Kendall τ of the top 15 vs the baseline.

---

### 3.12 Implementation decisions (v0.3)
- **Inputs:** only `supported` claims; funding_round / revenue / acquired / prior_exit also need
  `meta.corroboration.corroborated`. Missing features contribute 0 and add nothing to n.
- **Percentile:** `(average rank - 0.5) / n` over the non-missing values of the pool; ties share ranks.
- **T inputs:** prior_vc_backed = prior founder role at a company with corroborated rounds in our data or on the
  scaled-edtech list; operator_pedigree = manager+ role at a scaled edtech (list in weights.yaml);
  accelerator_or_award = supported GitHub repo with >= `accelerator_github_min_stars` (technical depth; accelerator
  membership is discovery data, not yet a claim); top_investor = corroborated round naming a listed investor.
  n_T = verified signals behind the founder's features.
- **M:** funding velocity (rounds in 730 days), open roles, news recency, GitHub stars (log); each pct x
  `0.5^(age/180)`, averaged over available features. n_I = momentum features + 1 if N exists.
- **N:** company descriptions embedded (Gemini), L2-normalized, KMeans k by cosine silhouette (k 2-12), crowded =
  clusters at/above the median size; growth = slope of rounds per quarter (8 quarters) per cluster.
- **F lenses (v0.4):** acquire = capability overlap x geo x size_factor x (0.6 if very early); size_factor = 0 above
  the ceiling, 1 when size is verified, 0.7 when unknown. partner = segment overlap x (0.5 + 0.5 x capability
  overlap) x geo. co_build = capability overlap x geo x (1 very early / 0.75 early / 0.5 later) + 0.2 if founder
  T >= 0.4 and early. Very early = raised < $3M (and only seed-type stages), else only seed-type stages, else founded
  <= 3 years ago. Early = raised < $10M and stages up to pre-Series A.
- **Size (v0.4):** the largest of corroborated rounds (sum), a supported `total_funding` statement and the largest
  supported round; used by the TOO_LARGE gate and the acquire lens. Single-source evidence is accepted for these
  because wrongly keeping a giant costs more than wrongly dropping one. Unknown size is shown as "Size not verified".
- **Location (v0.4, `features.infer_geo`):** from supported claims, strongest first: country/city claim (decides
  india/outside), Indian registry listing (Tofler/Zauba), an evidence quote such as "Bengaluru-based" or
  "headquartered in Pune", an amount stated in INR, a .in domain; else unknown. The basis is stored and shown.
- **Founder records (v0.4):** founders of the same company whose names are the same person in another order
  ("Minh Thu Do" / "Do Minh Thu") or a contained 2+ token name ("Lam Do" / "Do Ngoc Lam") are merged at the end of
  resolve (`screener founders dedupe`); claims, links and discoveries move to the kept record.
- **Explain (v0.4):** besides why/fit lines, each shortlisted company's top 2 founders get a <= 25-word line from
  their verified claims (same validation: claim ids, numbers), and up to 3 exclusions are curated as "left out"
  picks, one per category (already a scaled player / not enough verified evidence / a close call), India-located
  first, each with a <= 40-word cited line.
- **Targeted lookups (v0.4, `enrich/lookups.py`):** inside the enrich stage, for the top 80 of the latest scored run
  and only where data is missing: size (total funding / headcount / rounds), location (city / country) and founder
  background (prior roles, exits, education; interviews and profiles, LinkedIn via snippets only). Recorded in
  `companies.lookups` / `founders.lookups`, never repeated; the facts are verified like any other claim.
- **C:** expected claims = 10 per company + 4 per founder (max 2 founders) (assumption, weights.yaml).
- **Gates:** "active" = liveness is not INACTIVE (UNKNOWN passes; liveness has three states). upGrad itself is
  UPGRAD_OWNED (`exclusions.yaml self_names`). Shortlist eligibility (rule 2): identity-verified founder with a
  non-LinkedIn current_role claim, company existence (website/registry claim or any supported claim from the
  company's own domain), and >= 3 verified track-record/interest claims; otherwise scored but UNVERIFIABLE.
- **Pairwise:** top 60 gate-passers by Final; round 1 rank neighbours (window 3), round 2 closest BT estimates
  (~10 per item); each pair judged in both orders by Gemini-smart on verified-claim dossiers + the gap map;
  disagreement = tie (one win each way); choix ILSR; Final' = 0.7 Final + 0.3 pct(BT) (no BT -> 0). Judgments are
  stored in `judgments` and cached.
- **MMR:** k = 15 over shortlist-eligible companies, lambda 0.75, sim = cosine + 0.1 same fit_type, at least 2 per
  fit type when available. Not selected: DUPLICATE_THESIS if Final' >= the lowest selected, else LOW_FIT.
- **Calibration:** recall@50 of `seeds.yaml` positives in the latest run; stability = Kendall tau of the baseline
  top 15 under 20 trials of U(0.8, 1.2) multipliers on every T weight, I weight, Final exponent and the team bonus.

## 4. Google Sheet contract

Tabs: **Shortlist**, **Left out**, **Long list**, **Claims**, **Run log**.

### 4.1 Shortlist columns (in order)
`Rank | Founder(s) | Company | Website | Fit type | Final | T | I | F | Confidence | Why they made the cut | How they fit upGrad | Evidence 1 | Evidence 2 | Evidence 3 | LinkedIn URL | Last verified | Reviewer label | Reviewer note | entity_id (hidden)`

- Evidence columns hold source URLs of supported claims.
- "Reviewer label" and "Reviewer note" are human-edited; the pipeline reads them back into `feedback` and never overwrites them.
- `entity_id` is a hidden column used to join rows across runs.

### 4.2 Left out

Reason codes:

| Code | Meaning |
|---|---|
| `TOO_LARGE` | Above the size ceiling |
| `COMPETITOR` | Competitor at scale |
| `UPGRAD_OWNED` | Already owned by/part of upGrad |
| `UNVERIFIABLE` | Identity, role, existence or minimum claims not verified |
| `INACTIVE` | Company not active |
| `LOW_FIT` | Passed gates but low score / below cutoff |
| `NOT_EDUCATION` | Not education-relevant |
| `DUPLICATE_THESIS` | Removed by MMR as redundant with a higher-ranked pick |

Left out columns map 1:1 to `v_left_out`: Company, Founder(s), Reason code, Explanation,
Would have ranked, Source, entity_id (hidden). The Sheet and the dashboard always show the same run.

### 4.2a When and how the Sheet is written
- Written automatically at the end of every full run (`screener run`, "Run now", the schedule) and every re-score
  (`screener rescore`, the AI Policy's "Save & re-score"); `screener publish` re-sends the latest run. Publishing is
  fail-soft: the run stands if the Sheet is unreachable.
- Every publish re-applies the layout (idempotent): tabs in the order Shortlist, Left out, Long list, Claims, Run log;
  frozen, brand-red header with a note on each scored column (hover to read what T, I, F, Final, the reason codes mean);
  a filter on the header row; column widths; wrapped long text, one-line URLs; number formats (Final 0.000, sub-scores
  0.00); fit-type colours; a reviewer dropdown. Tabs grow past their initial 1,000 rows as needed.
- Left out lists the three curated picks first (with their cited line), then every other exclusion by would-have-ranked.

### 4.3 Long list, Claims, Run log
- **Long list**: every scored candidate with sub-scores and fit type.
- **Claims**: every claim behind shortlisted/left-out entities (field, value, source URL, evidence span, status).
- **Run log**: run id, timestamps, counts per stage, cost, quota usage, provider errors.

---

## 4a. Universe and tiers (lakhs-scale)

| Tier | Size | Work | Cost / company |
|---|---|---|---|
| 0 Universe | lakhs | Bulk registries: MCA company master (data.gov.in API), DPIIT startups (Dataful CSV), YC directory, scout discoveries -> `registry_entities` | ~0 |
| Validation | lakhs | Row checks (CIN format, dates, status, name); DPIIT vs MCA cross-check by CIN (name/state agreement) | ~0 |
| 1 Triage | lakhs -> thousands | Code only: education NIC in CIN (85 / 80), registry-name vocabulary, DPIIT sector, scout/accelerator hits, cross-validation, website known, recency; inactive/invalid never crawled -> `prospects` | ~0 |
| 2 Light | thousands | Website (registry value validated against the homepage, else one gentle free search) + 1 Gemini-fast call grounded on the homepage -> verdict fit / not_fit / unknown; fit -> promoted to `companies` | 1 fetch + 1 LLM |
| 3 Deep | hundreds | `enrich` + `verify` for promoted companies | full |
| 4 Score | -> 15 | SPEC section 3 | judge only |

- **Never paid twice:** every verdict is stored on the prospect with the registry content hash; it is reused until the
  rows change or `light.refresh_days` passes. Queue tasks are unique per (kind, subject) and finished tasks are
  never re-queued. Fetch/search/LLM caches sit underneath.
- **Real time:** `screener worker` claims tasks by priority (row locks, several workers OK) and commits each result
  immediately; fit companies flow straight into deep enrichment and verification. Budgets: `light.daily_llm_budget`
  Gemini calls/day (then light tasks resume next day), `light.search_rpm` for free search. No paid search.
- **MCA rows** that cannot reach the triage threshold (no education NIC/keyword, no DPIIT/scout hit) are stored but
  never loaded for triage (SQL pre-filter).

## 5. Sources and the LinkedIn policy

### 5.1 Sources
Configured in `config/sources.yaml`: RSS feeds (Indian startup/edtech press), accelerator cohort pages,
VC portfolio pages, curated lists, and search queries run through the search router
(SearXNG → ddgs → Exa → Tavily → SerpAPI → Gemini grounding). Every fetch obeys robots.txt,
per-domain rate limits and the cache, and uses a descriptive User-Agent.

### 5.2 LinkedIn policy
- **Never** fetch or scrape any `linkedin.com` page. **Never** log in anywhere.
- LinkedIn information may be used **only** from search-engine result snippets (stored as claims with
  `source_type = search_snippet`, `source_url` = the LinkedIn URL, `evidence_span` = the snippet text).
- A LinkedIn profile URL may be stored as the founder's identity URL.
- Snippet-only claims need corroboration from a non-LinkedIn source to count toward the Shortlist gate.

### 3.13 AI Policy (v0.4)
- The editable policy (`src/screener/policy.py`) is a versioned overlay on `weights.yaml`, `exclusions.yaml` and
  `upgrad_gap_map.yaml`, stored in `policy_versions`. The YAML stays the default; the active version wins.
- **Categories are generic**: each is a lens `base = cap_w*cap + seg_w*seg + both_w*seg*cap`, then x geo x size
  factor x stage factor, plus a founder boost. The defaults (Acquire, Partner, Co-build) reproduce §3.5 exactly.
  The best enabled category names the fit type.
- **Filters** toggle the gates of §3.7 and add post-ranking filters; a company removed by them is left out with
  reason `POLICY_FILTER`. `ACQUIRED` is a gate: any supported "acquired" claim about the company.
- **Preview** reruns gates, lenses, ranking and MMR on stored features and Bradley–Terry percentiles, with no LLM
  calls. A saved policy takes effect on the next `score` / `rescore` (a new run_id); old runs keep their snapshot.
- **Where it applies**: score and explain, and also enrich (orchestrator, `screener enrich`) and the registry worker
  (re-read every minute), so added business units or capabilities are extracted from pages from then on.
- **Judge fallback**: if fewer than `pairwise.min_answered_share` of the judge's comparisons come back (e.g. the
  Gemini quota), scoring reuses the newest scored run's Bradley-Terry percentiles instead of fitting BT on missing
  data, so a re-score without Gemini matches the preview.
- **Must consider (v0.4):** `lists.must_consider` names are pinned first in MMR (best score first, up to the
  shortlist size) when they pass every gate, policy filter and shortlist check; otherwise they stay in Left out with
  the reason. Reviewer picks (`config/reviewer_picks.yaml`, `screener reviewer import`) enter as discoveries with
  scout `reviewer` and their origin; their category and note are the reviewer label/note (`feedback`, shown as the
  reviewer's view on cards and in the Sheet, never as a claim). A Sheet entry wins over the file.
- The LLM never edits the policy; people do, through the structured form.

### 5.3 X (Twitter) policy (v0.4)
- x.com / twitter.com are **never fetched**: x.com's robots.txt disallows automated access (including
  `publish.x.com/oembed`, checked 2026-10-25) and the API is paid. X is used only through search-engine results,
  exactly like LinkedIn (snippet claims verified against the stored snippet).
- **Scout `x`**: profile results ("Name (@handle) / X") whose bio names a company ("Founder @Acme") become
  discoveries; the x.com profile URL is the founder's identity key. Yield is low: search engines index few X bios.
- **Lookups**: per founder, an X profile is attached only if the name is the same person AND the bio names the
  company (common names would otherwise match strangers); the bio's roles/education become snippet claims. Per
  company, X posts about funding, launches or hiring become snippet claims (funding still needs corroboration).
- Like LinkedIn, an X profile or post **never verifies founder identity on its own**.
