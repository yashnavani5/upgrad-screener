# Runbook: upGrad Founder Screener

How to run, resume, schedule and read a pipeline run. Architecture: [ARCHITECTURE.md](ARCHITECTURE.md).
Rules: [../CLAUDE.md](../CLAUDE.md).

## 1. What a run is

`screener run` executes seven stages under one `run_id`:

**discover → resolve → enrich → verify → score → explain → publish**

Each stage writes a checkpoint into `runs.checkpoints`:

| Status | Meaning | On `--resume` |
|---|---|---|
| `done` | finished | skipped |
| `partial` | finished part of the work (budget guard deferred the rest) | run again (continues) |
| `skipped` + retry | not run for a fixable reason (budget, missing Sheet credentials) | run again |
| `skipped` | skipped by choice (`--no-publish`) | stays skipped |
| `failed` | raised an error (logged; the run continued where safe) | run again |
| `running` | the process died during this stage | run again |

Run status: `succeeded` (all done), `partial` (something deferred/skipped/failed but a scored result exists),
`failed` (scoring did not complete). A run whose heartbeat is older than 15 minutes is shown as failed.

**Incremental (default):** discovery and resolution are idempotent; enrichment only touches companies never
enriched or enriched more than `enrich_stale_days` (30) ago; verification only checks unverified claims. A
resumed run never redoes work finished earlier in the same run. `--full` re-enriches everything (still never
redoing this run's finished companies).

**Spend caps (hard):** every Gemini call reserves its estimated USD cost before it is sent and is refused
(never sent) if it would cross a cap: `RUN_BUDGET_USD` per process, and `providers.yaml
budget.gemini_daily_usd_limit` / `gemini_monthly_usd_limit` shared by every process (run, worker, enrich,
ai-triage, dashboard rescores; UTC day/month, counted in `provider_quota` as `gemini:usd`). Cost uses
`llm.prices_per_million_tokens` and includes thinking tokens and the grounding fee; a cap with a null price
fails closed. The worker ignores the per-run caps, requeues capped tasks without spending an attempt and
sleeps until the cap resets. `screener doctor` shows today's and this month's spend. The dashboard's Ask box
calls Gemini from `web/` and is not counted.

**Budget guard:** before each LLM-heavy stage the remaining Gemini budget sizes the work (per-run cap
`RUN_MAX_LLM_CALLS`, the USD caps above via `budget.estimates.usd_per_call`, optional daily call cap
`budget.gemini_daily_limit`). Work that does not fit is deferred and recorded in the checkpoint (`partial`,
"deferred by the budget guard"), so no cap is hit mid-stage. Resume later (or the next day) to continue.
Paid search providers are capped per month by the router.

## 2. Local run

### First time

```powershell
# PowerShell
Copy-Item .env.example .env                                   # fill in keys (section 4)
Copy-Item docker\searxng\.env.example docker\searxng\.env
docker compose up -d                                          # Postgres :5433 + SearXNG :18080
uv sync
uv run playwright install chromium
uv run screener db init                                       # roles + tables + views
uv run screener doctor                                        # every provider: enabled / working / quota left
```

```bash
# bash
cp .env.example .env
cp docker/searxng/.env.example docker/searxng/.env
docker compose up -d
uv sync
uv run playwright install chromium
uv run screener db init
uv run screener doctor
```

### Run, watch, resume

```powershell
uv run screener run --top 150            # full chain, incremental
uv run screener watch                    # in a second terminal: live stages, calls/min, claims, queue (Ctrl+C to exit)
uv run screener run --resume <run_id>    # continue a killed/partial run from its last completed stage
```

```bash
uv run screener run --top 150
uv run screener watch
uv run screener run --resume <run_id>
```

Useful flags: `--full`, `--no-publish`, `--no-pairwise`, `--no-websites`, `--scouts news_rss,hn`,
`--limit-per-scout 30`. The command prints a stage table and, if the run is not `succeeded`, the exact resume
command.

### In Docker

```powershell
docker compose --profile pipeline build
docker compose run --rm pipeline doctor
docker compose run --rm pipeline run --top 150
docker compose --profile pipeline up -d worker      # always-on real-time universe worker
```

(The same commands work in bash.) Inside Docker the pipeline reaches Postgres and SearXNG by service name; a
service-account JSON given as a path must be mounted (see the comment in `docker-compose.yml`) or passed inline.

### Individual stages

`discover`, `resolve`, `enrich --top N`, `verify --top N`, `score`, `calibrate`, `explain`, `publish`,
`dossier --n 5`, `universe build`, `worker`. See `uv run screener --help`.

## 3. Scheduled and on-demand runs (GitHub Actions)

Workflow: [`.github/workflows/screener.yml`](../.github/workflows/screener.yml).

- **Daily:** 01:30 UTC (07:00 IST), incremental, `top_n` 150.
- **On demand:** GitHub → Actions → *screener* → *Run workflow* (inputs `top_n`, `mode`), or the dashboard's
  "Run now" (`POST /api/runs` → `workflow_dispatch`).
- Runs never overlap (`concurrency: screener`); a second trigger waits.
- `.cache` (fetch/search/LLM/embedding caches) is restored and saved with `actions/cache`, so repeated runs reuse
  earlier work. Logs are uploaded as the artifact `screener-logs-<run id>` (30 days).
- SearXNG is started with `docker run` after checkout (service containers cannot mount repo files).

To resume a failed scheduled run, run locally against the same database: `uv run screener run --resume <run_id>`
(the run id is in the Sheet's Run log tab and in the job log).

## 4. Secrets and variables

| Name | Where | Required | Notes |
|---|---|---|---|
| `DATABASE_URL` | `.env`, GitHub secret | yes | `pipeline_rw` role. Locally `localhost:5433`; Actions needs a reachable host |
| `DATABASE_ADMIN_URL` | `.env` only | for `db init` | superuser, creates roles |
| `POSTGRES_*`, `PIPELINE_RW_PASSWORD`, `WEB_RO_PASSWORD` | `.env` | local | container + roles |
| `GEMINI_API_KEY` | `.env`, secret | yes | |
| `GEMINI_MODEL_FAST` / `_SMART` / `EMBED_MODEL` | `.env`, GitHub *variables* | yes | `doctor` lists what your key can use |
| `SEARXNG_SECRET` | `docker/searxng/.env`, secret | yes | any long random string |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | `.env` (path), secret (inline JSON) | to publish | share the Sheet with the SA email as Editor |
| `GOOGLE_SHEET_ID` | `.env`, secret | to publish | the long id in the Sheet URL |
| `USER_AGENT_CONTACT` | `.env`, variable | strongly | shown in the crawler User-Agent |
| `RUN_MAX_LLM_CALLS` | `.env`, variable | no (2000) | per-run Gemini call cap (budget guard) |
| `RUN_BUDGET_USD` | `.env`, variable | no (5) | per-run Gemini spend cap (hard); day/month caps live in `providers.yaml budget` |
| `EXA_API_KEY`, `TAVILY_API_KEY`, `SERPAPI_API_KEY`, `JINA_API_KEY` | `.env`, secrets | no | free tiers are capped automatically |
| `GITHUB_TOKEN`, `PRODUCTHUNT_TOKEN`, `PDL_API_KEY` | `.env`, secrets | no | enrichment extras |
| `DATA_GOV_IN_API_KEY`, `DPIIT_STARTUPS_CSV` | `.env`, secret | universe | MCA / DPIIT registries |
| `DASHBOARD_*`, `DATABASE_URL_RO`, `GITHUB_DISPATCH_TOKEN` | `web/.env.local`, Vercel | dashboard | never mixed with the backend file |

### Google Sheet setup (service account, about 10 minutes)

The publisher uses a **service account** (`gspread.service_account_from_dict` + `open_by_key`). OAuth client ids
(`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`) are not used.

1. Google Cloud console → pick or create a project → *APIs & Services → Library* → enable **Google Sheets API**
   (and **Google Drive API**, harmless and avoids permission surprises).
2. *IAM & Admin → Service accounts → Create service account* (e.g. `upgrad-screener-publisher`); no roles needed.
3. Open it → *Keys → Add key → Create new key → JSON*. Save the file outside the repo, e.g.
   `C:/Users/<you>/.secrets/upgrad-screener-sa.json`. If key creation is blocked, the organisation policy
   `iam.disableServiceAccountKeyCreation` is on: use a project outside that organisation or ask the admin.
4. Create the Sheet (sheets.new), click *Share*, add the service account's `client_email` (from the JSON) as
   **Editor**. The Sheet id is the part of the URL between `/d/` and `/edit`.
5. `.env`: `GOOGLE_SERVICE_ACCOUNT_JSON=C:/Users/<you>/.secrets/upgrad-screener-sa.json` and `GOOGLE_SHEET_ID=<id>`.
   `web/.env.local`: `GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/<id>/edit` (the dashboard link).
   Docker: mount the file (see the comment in `docker-compose.yml`) and point the variable at `/run/secrets/sa.json`.
   GitHub Actions: store the JSON itself, on one line, as the secret.
6. Check and publish: `uv run screener doctor --live` (google_sheets: opens the Sheet), then `uv run screener publish`.
   The five tabs are created, formatted and filled on the first publish; later publishes update rows in place and
   never touch the reviewer columns.

## 5. Reading a run

- **Terminal:** the stage table at the end of `screener run`, or `screener watch` while it runs.
- **Sheet → Run log tab:** one row per run: run_id, start/end, status, candidates, companies, verified claims,
  shortlisted, provider quota used, cost.
- **Database:** `runs` (status, current_stage, stage_progress, heartbeat_at, checkpoints, error_summary),
  `api_calls` (every external call with provider, status, latency, tokens), `run_funnel` (stage counts).

```sql
select id, status, current_stage, checkpoints, error_summary from runs order by started_at desc limit 5;
select provider, status, count(*), avg(latency_ms)::int from api_calls
 where created_at > now() - interval '1 day' group by 1, 2 order by 3 desc;
```

- **Logs:** `.cache/logs/screener.log` (every external call; rotating 5 MB x 3). In Actions: the
  `screener-logs-*` artifact.

## 6. Common problems

| Symptom | Cause | Fix |
|---|---|---|
| `searxng 0 results` in doctor | upstream engines rate-limited (CAPTCHA) after heavy use | wait 30-60 min; ddgs fallback keeps working; lower `light.search_rpm` |
| SearXNG unreachable after a Windows restart | Hyper-V reserved the port range | it uses 18080; change `SEARXNG_HOST_PORT` + `SEARXNG_URL` if needed |
| Run `partial`, "deferred by the budget guard" | Gemini budget for the run/day/month reached | `screener run --resume <run_id>` after the reset, or raise `RUN_BUDGET_USD` / the `providers.yaml budget` caps |
| publish `skipped` | Sheet credentials missing | set `GOOGLE_SERVICE_ACCOUNT_JSON` + `GOOGLE_SHEET_ID`, then `screener publish` |
| Run stuck `running` | process killed | it shows `failed` after 15 min; `screener run --resume <run_id>` |

## Windows: "EACCES: permission denied" on a local port (dashboard :3001, SearXNG, Postgres)

Cause: the Windows dynamic TCP port range on this machine started at 1024 (default is 49152), so Hyper-V / WSL /
WinNAT reserve random blocks of low ports at boot (`netsh int ipv4 show excludedportrange protocol=tcp`), and a
fixed port such as 3001 can become unbindable after a restart. Fix once, in an **administrator** PowerShell, with
no pipeline run in progress (restarting WinNAT briefly drops Docker/WSL networking):

```powershell
netsh int ipv4 set dynamic tcp start=49152 num=16384
netsh int ipv6 set dynamic tcp start=49152 num=16384
net stop winnat
net start winnat
```

Then restart Docker Desktop if containers lost their ports, and start the dashboard: `cd web && npm start` (:3001).
