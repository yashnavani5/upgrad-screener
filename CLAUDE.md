# upGrad Founder Screener: engineering rules

**Mission.** Build an agentic AI pipeline that discovers education founders and companies (India-first),
verifies every fact against a source URL, scores them against explicit criteria, and publishes a live
Google Sheet shortlist (top 10–15 plus a "Left out" tab with reasons) for upGrad's partnerships / M&A /
co-build team. It must run end to end on real data. No mock data in the pipeline, ever.

Full definitions, data model and scoring math live in [docs/SPEC.md](docs/SPEC.md). Read it before
changing scoring, models or the Sheet contract.

## Non-negotiables
1. Every fact is a Claim with `source_url`, `retrieved_at`, `evidence_span` and `confidence`. No source = the fact doesn't exist.
2. A founder reaches the Shortlist only if identity, current role and company existence are verified, plus ≥3 verified track-record/interest claims.
3. The LLM extracts, verifies and explains. Code scores. Never ask the LLM for a final numeric score.
4. Never scrape linkedin.com pages or log in anywhere. LinkedIn is used only via search-engine result snippets and as an identity URL.
5. Be polite: per-domain rate limits, respect robots.txt, identify with a descriptive User-Agent, cache every fetch.
6. Never fabricate names, URLs, numbers or quotes. If unknown, store `null`.
7. No mock or fake data in pipeline code. Fixtures are allowed only under `tests/fixtures/`.

## Stack
- **Runtime:** Python 3.12, managed with uv. Typer CLI. Async I/O (httpx). Pydantic v2 models.
- **DB:** Postgres 17 + pgvector in Docker (`pgvector/pgvector`; local now, hosted later. Not Neon/Supabase).
  SQLAlchemy 2 + psycopg 3, Alembic migrations (`DATABASE_URL`); SQLite allowed for tests.
- **LLM:** Google Gemini via the `google-genai` SDK. Model names come from config
  (`GEMINI_MODEL_FAST`, `GEMINI_MODEL_SMART`, `GEMINI_EMBED_MODEL`). Never hardcode model names.
- **Search router (in order):** SearXNG (self-hosted Docker) → ddgs → Exa → Tavily → SerpAPI →
  Gemini grounding (if available). Each provider is optional; a missing key disables that provider.
- **Fetching:** httpx + trafilatura; Playwright fallback for JS pages; Jina Reader (`r.jina.ai`) as last resort.
- **Embeddings:** Gemini embeddings by default; fastembed (`BAAI/bge-small-en-v1.5`) as CPU fallback. No GPU, no PyTorch.
- **Stats:** numpy, scipy, scikit-learn, rapidfuzz, choix.
- **Publishing:** gspread with a Google service account.
- **Dashboard:** Next.js in `/web` (local now, Vercel later); reads Postgres views; triggers runs.

## Architecture
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before any backend, DB or integration work.
- Read [docs/FRONTEND.md](docs/FRONTEND.md) and [docs/BRAND.md](docs/BRAND.md) before any `/web` work.
- Operating runs (local, Docker, GitHub Actions, resume, secrets): [docs/RUNBOOK.md](docs/RUNBOOK.md).
- The `v_*` views and `src/screener/contracts.py` are the API between backend and frontend.
  The frontend never queries raw tables.
- After any contract change, regenerate types (`screener export-schema`, `npm run gen:types`) and commit
  them in the same change. See [contracts/README.md](contracts/README.md).

## Environment
- Dev machine is Windows with Docker + WSL. Use `pathlib` everywhere; no shell-specific assumptions in Python.
- Docs give both PowerShell and bash snippets.
- CPU only.

## Repo layout
```
src/screener/{config,db,models,cache,ratelimit,http,llm,search,embed,urls,telemetry}.py
src/screener/migrations/   # Alembic (v_* views in 0002_dashboard_views.py)
src/screener/scouts/  resolve/  enrich/  verify/  score/  explain/  publish/
src/screener/orchestrator.py
src/screener/cli.py
config/*.yaml          # weights, gap map, exclusions, seeds, sources (editable assumptions)
docs/SPEC.md           # the spec: definitions, data model, scoring, Sheet contract
docs/ARCHITECTURE.md   # components, views contract, run control flow, secrets, local dev
docs/FRONTEND.md, docs/BRAND.md
src/screener/contracts.py   # Pydantic rows for v_* views
contracts/             # generated JSON Schema (do not edit)
tests/                 # tests/fixtures/ is the ONLY place fixtures may live
web/                   # Next.js dashboard
.github/workflows/
docker-compose.yml, docker/postgres/init/, docker/searxng/
```

## Config files
- `config/weights.yaml`: every weight and constant from SPEC §3. Code reads these; no magic numbers in code.
- `config/upgrad_gap_map.yaml`: upGrad business units and wanted capabilities (fit lenses).
- `config/exclusions.yaml`: competitors at scale, upGrad-owned companies, size ceiling.
- `config/seeds.yaml`: calibration positives/negatives.
- `config/sources.yaml`: RSS feeds, accelerators, VC portfolios, lists, search queries.
- `config/universe.yaml`: registries (MCA/DPIIT), validation, triage weights, light-tier budget, worker.
- `config/enrich.yaml`: INR rate (with source date), page/search limits, verification and corroboration rules, PDL budget.
- `config/resolve.yaml`: entity-resolution thresholds, trailing suffixes, shared/non-company domains.
- `config/providers.yaml`: search strategies, provider quotas (Gemini is not rate limited), fetch/cache settings, Gemini prices and spend caps.
- `config/reviewer_picks.yaml`: companies the upGrad team asked to consider (names, founders, source URLs; category + note become the reviewer label/note, never facts).
- Env files: `.env` (backend), `web/.env.local` (dashboard), `docker/searxng/.env` (SearXNG). Never mix them.

## Commands (keep this section updated)
```bash
docker compose up -d                    # Postgres (host :5433) + SearXNG (:18080); ask before building/rebuilding
uv sync
uv run playwright install chromium
uv run screener db init                 # roles + Alembic: tables + v_* views + grants
uv run screener doctor                  # provider/dependency health (--live spends quota)
uv run screener search "query" --strategy cheap
uv run screener fetch https://example.com
uv run screener sources resolve-feeds   # find + verify news RSS feeds, save to config/sources.yaml
uv run screener discover --scouts all --limit-per-scout 30
uv run screener discoveries sample --n 10
uv run screener resolve                 # discoveries -> companies/founders (idempotent; --no-websites)
uv run screener reviewer import         # reviewer picks -> discoveries (scout 'reviewer') -> resolve -> reviewer label/note
uv run screener reviewer status         # each pick's company record, enrich/verify state, label
uv run screener enrich --top 150        # grounded Claims per company (--company NAME, repeatable)
uv run screener verify --top 150        # fresh fetch + independent verifier + rules
uv run screener dossier --n 5           # supported claims with URLs
uv run screener score                   # SPEC 3 scores + pairwise judge + MMR shortlist (new run_id)
uv run screener calibrate               # recall@50 on seeds + Kendall-tau stability
uv run screener founders dedupe         # merge same-person founder records (also runs at the end of resolve)
uv run screener seeds refresh           # auto seeds from the latest scored run (also runs after every score stage)
uv run screener universe load-mca       # MCA company master via data.gov.in API (resumable; DATA_GOV_IN_API_KEY)
uv run screener universe load-dpiit     # DPIIT startups CSV (DPIIT_STARTUPS_CSV)
uv run screener universe load-yc        # all YC education companies (yc-oss)
uv run screener universe build          # validate + cross-validate + prospects + code triage + queue light tier
uv run screener worker                  # real-time queue worker: light -> deep -> verify (runs forever; --until-empty)
uv run screener universe status
uv run screener universe ai-triage      # Gemini routing guess over queued registry prospects (--batch 50 --concurrency 12)
uv run screener policy show             # active AI Policy (YAML defaults until a version is saved)
uv run screener policy preview --file draft.json   # shortlist diff for a draft, no LLM calls
uv run screener policy save --file p.json --note "..."   # new version (--draft to save without activating)
uv run screener policy activate <version>          # roll back / forward
uv run screener rescore                 # score + explain under the active policy (new run_id), then publish to the Sheet
uv run screener audit sample --n 30     # random claims to docs/audit/claims-sample.csv for manual checking
uv run screener explain                 # why/fit lines (claim-cited, validated) + evidence for the shortlist
uv run screener publish                 # latest run -> Google Sheet (5 tabs)
uv run screener run --top 150           # full chain under one run_id (incremental; --full)
uv run screener run --resume <run_id>   # continue a killed/partial run from its last completed stage
uv run screener watch                   # live view of any running job
docker compose --profile pipeline build # pipeline image (Playwright base pinned to uv.lock)
docker compose run --rm pipeline run --top 150    # same chain inside Docker
docker compose --profile pipeline up -d worker    # always-on real-time worker
uv run pytest -q                        # Postgres view tests need TEST_DATABASE_URL
uv run screener --help
uv run screener export-schema           # contracts/*.json
cd web && npm run gen:types && npm run dev
cd web && npm run build && npm start    # production dashboard on :3001 (basic auth from web/.env.local)
cd web && npm run test:e2e              # Playwright vs the running dashboard + real DB; E2E_REQUIRE_DATA=1 after a scored run
```

## Working style
- Plan before coding; build in small vertical slices; write tests for pure logic (scoring, resolution, grounding).
- After each task, run the tests and the task's acceptance checks, and report the actual results.
- Log every external call with provider, status and latency. Track per-run cost and quota usage in the `runs` table.
- Fail soft: one source failing must never crash a run. Log it and continue.
