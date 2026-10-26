"""`screener` command line."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from screener import __version__
from screener.config import ROOT, get_config

app = typer.Typer(help="upGrad Founder Screener", no_args_is_help=True, add_completion=False)
db_app = typer.Typer(help="Database commands", no_args_is_help=True)
app.add_typer(db_app, name="db")
sources_app = typer.Typer(help="Manage config/sources.yaml", no_args_is_help=True)
app.add_typer(sources_app, name="sources")
discoveries_app = typer.Typer(help="Inspect stored discoveries", no_args_is_help=True)
app.add_typer(discoveries_app, name="discoveries")
universe_app = typer.Typer(help="Lakhs-scale universe: registries, validation, triage", no_args_is_help=True)
app.add_typer(universe_app, name="universe")
seeds_app = typer.Typer(help="Calibration seeds in config/seeds.yaml", no_args_is_help=True)
founders_app = typer.Typer(help="Founder records", no_args_is_help=True)
audit_app = typer.Typer(help="Manual accuracy audit", no_args_is_help=True)
policy_app = typer.Typer(help="AI Policy: versioned filters, weights, categories (dashboard /policy)", no_args_is_help=True)
app.add_typer(policy_app, name="policy")
app.add_typer(audit_app, name="audit")
app.add_typer(founders_app, name="founders")
reviewer_app = typer.Typer(help="Reviewer picks in config/reviewer_picks.yaml", no_args_is_help=True)
app.add_typer(reviewer_app, name="reviewer")
app.add_typer(seeds_app, name="seeds")

# Windows consoles default to cp1252; scraped text (emoji, Indic scripts) must never crash the CLI.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
console = Console()


def _setup_logging(verbose: bool) -> None:
    cfg = get_config()
    log_dir = cfg.settings.cache_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_dir / "screener.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(cfg.settings.log_level.upper())
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    console_handler = RichHandler(console=Console(stderr=True), show_path=False)
    console_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root = logging.getLogger()
    root.handlers[:] = [file_handler, console_handler]
    root.setLevel(logging.DEBUG)
    for noisy in ("httpx", "httpcore", "primp", "trafilatura", "urllib3", "google_genai", "asyncio", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _version(value: bool) -> None:
    if value:
        console.print(f"screener {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logs on the console."),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="Show version."),
) -> None:
    _setup_logging(verbose)


# --- db --------------------------------------------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """Create roles (needs DATABASE_ADMIN_URL), tables, v_* views and web_ro grants. Safe to re-run."""
    from screener.db import init_db

    for step in init_db():
        console.print(f"[green]OK[/green] {step}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations only (tables, views, grants)."""
    from screener.db import current_revision, get_engine, upgrade

    engine = get_engine()
    upgrade(engine)
    console.print(f"[green]OK[/green] migrations at {current_revision(engine)}")


# --- search / fetch --------------------------------------------------------------------------------


def _quota_store() -> Any:
    from screener.db import DbQuotaStore, db_available, get_engine

    return DbQuotaStore(get_engine()) if db_available() else None


def _gemini() -> Any:
    cfg = get_config()
    if cfg.settings.gemini_api_key is None or not cfg.settings.gemini_model_fast:
        return None
    from screener.llm import GeminiClient

    gemini = GeminiClient(cfg)
    gemini.usage_store = _quota_store()  # shared day/month spend caps across every process
    return gemini


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query."),
    strategy: str = typer.Option("cheap", "--strategy", "-s", help="cheap | semantic | quality"),
    n: int = typer.Option(10, "--n", "-n", help="Max results."),
) -> None:
    """Run a query through the search router and print uniform results."""
    from screener.search import SearchRouter

    async def run() -> Any:
        quota = _quota_store()
        gemini = _gemini()
        async with httpx.AsyncClient(headers={"User-Agent": get_config().settings.user_agent}, timeout=20.0) as client:
            router = SearchRouter.from_config(client=client, quota=quota, gemini=gemini)
            try:
                return await router.search(query, strategy=strategy, n=n)
            finally:
                if gemini is not None:
                    await gemini.aclose()

    result = asyncio.run(run())
    table = Table(title=f'"{query}"  strategy={strategy}', show_lines=False)
    for col in ("#", "Provider", "Title", "URL"):
        table.add_column(col, overflow="fold")
    for r in result.results:
        table.add_row(str(r.rank), r.provider, r.title, r.url)
    console.print(table)
    for r in result.results:
        if r.snippet:
            console.print(f"[dim]{r.rank}.[/dim] {r.snippet[:220]}")
    console.print("[bold]Provider attempts:[/bold] " + "  ".join(
        f"{a.provider}={a.outcome}" + (f"({a.count})" if a.count else "") + (f" [{a.detail}]" if a.detail else "")
        for a in result.attempts
    ))
    if not result.results:
        raise typer.Exit(code=1)


@app.command()
def fetch(
    url: str = typer.Argument(..., help="URL to fetch."),
    chars: int = typer.Option(2000, "--chars", help="How much extracted text to print (0 = all)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the fetch cache."),
) -> None:
    """Fetch one URL politely and print the robots decision and the clean text."""
    from screener.http import Fetcher

    async def run() -> Any:
        async with Fetcher() as fetcher:
            return await fetcher.fetch(url, use_cache=not no_cache)

    r = asyncio.run(run())
    robots = r.robots
    console.print(f"[bold]URL:[/bold] {r.url}")
    console.print(f"[bold]Robots:[/bold] {'allowed' if robots and robots.allowed else 'disallowed' if robots else 'n/a'}"
                  + (f" ({robots.reason}; {robots.robots_url})" if robots else ""))
    console.print(f"[bold]Outcome:[/bold] {r.outcome}  [bold]Method:[/bold] {r.method}  [bold]Status:[/bold] {r.status}  "
                  f"[bold]Cached:[/bold] {r.from_cache}")
    console.print(f"[bold]Final URL:[/bold] {r.final_url}  [bold]Fetched at:[/bold] {r.fetched_at.isoformat()}")
    if r.error:
        console.print(f"[bold]Error:[/bold] {r.error}")
    text = r.text or ""
    console.print(f"[bold]Text:[/bold] {len(text)} chars\n")
    console.print(text if chars == 0 else text[:chars], markup=False, highlight=False)
    if not r.ok:
        raise typer.Exit(code=1)


# --- export-schema ---------------------------------------------------------------------------------


@app.command("export-schema")
def export_schema(out_dir: Path = typer.Option(ROOT / "contracts", help="Output directory.")) -> None:
    """Write JSON Schema for every v_* view row to contracts/*.json."""
    from screener.contracts import VIEW_CONTRACTS

    out_dir.mkdir(parents=True, exist_ok=True)
    for view, model in VIEW_CONTRACTS.items():
        schema = model.model_json_schema(mode="serialization")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["title"] = model.__name__
        schema["description"] = f"Row of {view}. Generated from src/screener/contracts.py; do not edit."
        (out_dir / f"{view}.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        console.print(f"[green]OK[/green] {out_dir / f'{view}.json'}")


# --- doctor ----------------------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    enabled: bool
    working: bool | None  # None = not tested
    quota: str = "-"
    details: str = ""


def _mark(value: bool | None) -> str:
    return {True: "[green]yes[/green]", False: "[red]no[/red]", None: "[yellow]not tested[/yellow]"}[value]


async def _doctor_checks(live: bool) -> list[Check]:
    from screener.db import (
        DbQuotaStore, current_revision, get_engine, head_revision, missing_roles, try_session,
    )
    from screener.search import DdgsProvider, SearxngProvider, SerpApiProvider, period_key
    from screener.ratelimit import get_limiter
    from sqlalchemy import text

    cfg = get_config()
    s = cfg.settings
    statuses = cfg.provider_statuses()
    limiter = get_limiter()
    checks: list[Check] = []

    # Config + cache
    checks.append(Check("config", True, True, details=f"{len(cfg.weights)} weight groups, gap map, exclusions, providers loaded"))
    try:
        s.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=s.cache_dir, delete=True):
            pass
        checks.append(Check("cache", True, True, details=str(s.cache_dir)))
    except OSError as exc:
        checks.append(Check("cache", True, False, details=str(exc)))

    # Postgres
    quota = None
    if statuses["postgres"].enabled:
        with try_session() as session:
            if session is None:
                checks.append(Check("postgres", True, False, details="unreachable (is the container running?)"))
            else:
                engine = get_engine()
                version = session.execute(text("SHOW server_version")).scalar()
                ext = session.execute(text(
                    "SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
                views = session.execute(text(
                    "SELECT count(*) FROM information_schema.views WHERE table_schema='public' AND table_name LIKE 'v\\_%'")).scalar()
                cur, head = current_revision(engine), head_revision(engine)
                roles_missing = missing_roles(engine)
                ok = cur == head and not roles_missing and ext is not None
                checks.append(Check(
                    "postgres", True, ok,
                    details=f"PG {version}, pgvector {ext or 'missing'}, migration {cur or 'none'}/{head}, "
                    f"{views} v_* views, roles {'ok' if not roles_missing else 'missing: ' + ','.join(roles_missing)}",
                ))
                quota = DbQuotaStore(engine)
    else:
        checks.append(Check("postgres", False, None, details=statuses["postgres"].reason))
    checks.append(Check("quota store", quota is not None, quota is not None,
                        details="provider_quota table" if quota else "DB unavailable: Exa/Tavily/SerpAPI/grounding disabled"))

    def quota_left(name: str) -> str:
        spec = cfg.providers.get("search", {}).get("providers", {}).get(name, {})
        if not spec.get("requires_quota"):
            return "free"
        if quota is None:
            return "unknown"
        limit = spec.get("limit")
        used = quota.used(name, period_key(spec.get("period")))
        return f"{limit - used:g}/{limit:g} per {spec.get('period')}" if limit is not None else f"used {used:g}"

    async with httpx.AsyncClient(headers={"User-Agent": s.user_agent}, timeout=15.0) as client:
        # SearXNG
        if s.searxng_url:
            try:
                res = await SearxngProvider(s.searxng_url, client, limiter).search("edtech India", 5)
                checks.append(Check("searxng", True, bool(res), quota_left("searxng"), f"{len(res)} results from {s.searxng_url}"))
            except Exception as exc:
                checks.append(Check("searxng", True, False, quota_left("searxng"), f"{type(exc).__name__}: {str(exc)[:120]}"))
        else:
            checks.append(Check("searxng", False, None, details=statuses["searxng"].reason))

        # ddgs
        try:
            res = await DdgsProvider(limiter).search("edtech India", 5)
            checks.append(Check("ddgs", True, bool(res), "free", f"{len(res)} results"))
        except Exception as exc:
            checks.append(Check("ddgs", True, False, "free", f"{type(exc).__name__}: {str(exc)[:120]}"))

        # Paid search providers: only tested with --live (a test costs quota), except SerpAPI's free account endpoint.
        for name in ("exa", "tavily"):
            st = statuses[name]
            checks.append(Check(name, st.enabled, None, quota_left(name) if st.enabled else "-",
                                "key set; run `doctor --live` to test (costs 1 call)" if st.enabled else st.reason))
        if live:
            from screener.search import SearchRouter

            router = SearchRouter.from_config(client=client, quota=quota)
            for c in checks:
                if c.name in ("exa", "tavily") and c.enabled:
                    r = await router.search("edtech India", strategy="semantic" if c.name == "exa" else "cheap", n=1)
                    hit = next((a for a in r.attempts if a.provider == c.name), None)
                    c.working = bool(hit and hit.outcome in ("ok", "cache"))
                    c.details = f"{hit.outcome if hit else 'not reached'}" + (f" ({hit.detail})" if hit and hit.detail else "")
                    c.quota = quota_left(c.name)
        if s.serpapi_api_key:
            try:
                acct = await SerpApiProvider(s.serpapi_api_key.get_secret_value(), client, limiter).account()
                left = acct.get("plan_searches_left", acct.get("total_searches_left"))
                checks.append(Check("serpapi", True, True, f"{quota_left('serpapi')} (account: {left} left)",
                                    f"plan {acct.get('plan_name', '?')}"))
            except Exception as exc:
                checks.append(Check("serpapi", True, False, quota_left("serpapi"), f"{type(exc).__name__}: {str(exc)[:120]}"))
        else:
            checks.append(Check("serpapi", False, None, details=statuses["serpapi"].reason))

        # Gemini
        if s.gemini_api_key:
            from screener.llm import GeminiClient, LLMConfigError

            gemini = GeminiClient(cfg)
            gemini.usage_store = _quota_store()
            try:
                spend = gemini.spend_summary()
                checks.append(Check("gemini_spend", True, all(cap is None or used < cap for used, cap in spend.values()),
                                    details="; ".join(f"{k} ${used:.2f} of {'no cap' if cap is None else f'${cap:g}'}"
                                                      for k, (used, cap) in spend.items())
                                    + (f"; per run ${s.run_budget_usd:g}" if s.run_budget_usd is not None else "")))
            except Exception as exc:
                checks.append(Check("gemini_spend", True, None, details=f"{type(exc).__name__}: {str(exc)[:120]}"))
            try:
                resolved = await gemini.ensure_models_available()
                checks.append(Check("gemini", True, True, details=", ".join(f"{k}={v}" for k, v in resolved.items())))
            except LLMConfigError as exc:
                checks.append(Check("gemini", True, False, details=str(exc)[:400]))
            except Exception as exc:
                checks.append(Check("gemini", True, False, details=f"{type(exc).__name__}: {str(exc)[:200]}"))
            if live and s.gemini_model_fast:
                ok = await gemini.probe_grounding()
                checks.append(Check("gemini_grounding", True, ok, quota_left("gemini_grounding"), gemini.grounding_reason or ""))
            else:
                checks.append(Check("gemini_grounding", True, None, quota_left("gemini_grounding"),
                                    "run `doctor --live` to probe (1 grounded call)"))
            await gemini.aclose()
        else:
            checks.append(Check("gemini", False, None, details=statuses["gemini"].reason))
            checks.append(Check("gemini_grounding", False, None, details=statuses["gemini_grounding"].reason))

        # Jina Reader
        if live:
            try:
                resp = await client.get("https://r.jina.ai/https://example.com", headers={"Accept": "text/plain"})
                checks.append(Check("jina", True, resp.is_success, details=f"HTTP {resp.status_code}; {statuses['jina'].reason}"))
            except httpx.HTTPError as exc:
                checks.append(Check("jina", True, False, details=str(exc)[:120]))
        else:
            checks.append(Check("jina", True, None, details=f"{statuses['jina'].reason}; `doctor --live` to test"))

    # Playwright (local browser launch, no network)
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            version = browser.version
            await browser.close()
        checks.append(Check("playwright", True, True, details=f"chromium {version}"))
    except Exception as exc:
        checks.append(Check("playwright", True, False, details=f"{str(exc).splitlines()[0][:160]} (run: uv run playwright install chromium)"))

    # fastembed
    model_dir = s.cache_dir / "fastembed_models"
    downloaded = model_dir.exists() and any(model_dir.iterdir())
    checks.append(Check("fastembed", statuses["fastembed"].enabled, statuses["fastembed"].enabled,
                        details=f"BAAI/bge-small-en-v1.5 {'cached' if downloaded else 'downloads on first use'}"))

    # Google Sheets
    if statuses["google_sheets"].enabled:
        try:
            info = s.service_account_info()
            detail = f"service account {info.get('client_email')}" if info else "no credentials"
            working: bool | None = None
            if live and info:
                import gspread

                gc = gspread.service_account_from_dict(info)
                sh = await asyncio.to_thread(gc.open_by_key, s.google_sheet_id)
                working, detail = True, f"{detail}; sheet '{sh.title}'"
            checks.append(Check("google_sheets", True, working, details=detail))
        except Exception as exc:
            checks.append(Check("google_sheets", True, False, details=f"{type(exc).__name__}: {str(exc)[:160]}"))
    else:
        checks.append(Check("google_sheets", False, None, details=statuses["google_sheets"].reason))

    for name in ("github", "producthunt", "pdl"):
        st = statuses[name]
        checks.append(Check(name, st.enabled, None, details=f"{st.reason} (used in later stages)"))

    if not s.user_agent_contact:
        checks.append(Check("user agent", True, False, details=f"USER_AGENT_CONTACT not set; UA: {s.user_agent}"))
    else:
        checks.append(Check("user agent", True, True, details=s.user_agent))
    return checks


@app.command()
def doctor(live: bool = typer.Option(False, "--live", help="Also test providers whose test call costs quota.")) -> None:
    """Check every provider and dependency: enabled / working / quota left."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    checks = asyncio.run(_doctor_checks(live))
    table = Table(title="screener doctor")
    for col in ("Component", "Enabled", "Working", "Quota left", "Details"):
        table.add_column(col, overflow="fold")
    for c in checks:
        table.add_row(c.name, _mark(c.enabled) if c.enabled else "[dim]no[/dim]", _mark(c.working) if c.enabled else "[dim]-[/dim]", c.quota, c.details)
    console.print(table)


# --- discovery -------------------------------------------------------------------------------------


@sources_app.command("resolve-feeds")
def sources_resolve_feeds() -> None:
    """Find each news site's RSS feed, verify it parses, and save verified feeds to config/sources.yaml."""
    from screener.config import CONFIG_DIR
    from screener.http import Fetcher
    from screener.scouts.feeds import resolve_feeds, save_feeds

    async def run() -> Any:
        async with Fetcher() as fetcher:
            return await resolve_feeds(fetcher, get_config().sources["news_rss"]["sites"])

    feeds, notes = asyncio.run(run())
    for n in notes:
        console.print(f"- {n}", markup=False)
    save_feeds(CONFIG_DIR / "sources.yaml", feeds)
    console.print(f"[green]OK[/green] saved {len(feeds)} verified feeds to config/sources.yaml")


@app.command()
def discover(
    scouts: str = typer.Option("all", "--scouts", help="'all' or comma-separated scout names."),
    limit_per_scout: int = typer.Option(30, "--limit-per-scout", help="Max candidates per scout."),
) -> None:
    """Run discovery scouts concurrently, store candidates in `discoveries`, print counts per scout."""
    import uuid

    from screener.http import Fetcher
    from screener.scouts import SCOUTS, ScoutContext, persist, run_scouts
    from screener.search import SearchRouter

    names = list(SCOUTS) if scouts == "all" else [s.strip() for s in scouts.split(",") if s.strip()]
    unknown = [n for n in names if n not in SCOUTS]
    if unknown:
        raise typer.BadParameter(f"unknown scouts {unknown}; choose from {list(SCOUTS)}")
    batch_id = uuid.uuid4()

    async def run() -> Any:
        cfg = get_config()
        quota = _quota_store()
        gemini = _gemini()
        async with httpx.AsyncClient(headers={"User-Agent": cfg.settings.user_agent}, timeout=20.0) as client, Fetcher(cfg) as fetcher:
            router = SearchRouter.from_config(cfg, client=client, quota=quota, gemini=gemini)
            ctx = ScoutContext(cfg=cfg, fetcher=fetcher, router=router, http=client, gemini=gemini)
            try:
                return await run_scouts([SCOUTS[n](ctx) for n in names], limit_per_scout)
            finally:
                if gemini is not None:
                    await gemini.aclose()

    reports = asyncio.run(run())
    for r in reports:
        try:
            r.inserted = persist(r.items, batch_id)
        except Exception as exc:
            r.error = (r.error or "") + f" persist failed: {exc}"
    table = Table(title=f"discover  batch={batch_id}  limit={limit_per_scout}")
    for col in ("Scout", "Status", "Found", "New", "Time", "Notes"):
        table.add_column(col, overflow="fold")
    for r in reports:
        status = "[dim]disabled[/dim]" if not r.enabled else "[red]error[/red]" if r.error else "[green]ok[/green]"
        notes = r.reason if not r.enabled else "; ".join(([r.error] if r.error else []) + r.notes)
        table.add_row(r.name, status, str(len(r.items)), str(r.inserted), f"{r.seconds:.0f}s", notes[:500])
    console.print(table)
    total = sum(len(r.items) for r in reports)
    active = sum(1 for r in reports if r.items)
    console.print(f"[bold]{total} candidates from {active} scouts[/bold] ({sum(r.inserted for r in reports)} new rows)")


@app.command()
def resolve(
    websites: bool = typer.Option(True, "--websites/--no-websites", help="Look up official websites for companies without a domain."),
    top: int = typer.Option(10, "--top", help="How many of the largest merge clusters to print."),
) -> None:
    """Resolve discoveries into canonical companies and founders (idempotent)."""
    from screener.db import get_engine
    from screener.embed import Embedder
    from screener.http import Fetcher
    from screener.resolve.service import resolve as run_resolve
    from screener.search import SearchRouter
    from sqlalchemy.orm import Session

    cfg = get_config()

    async def run() -> Any:
        quota = _quota_store()
        gemini = _gemini()
        embedder = Embedder(cfg, gemini=gemini)
        async with httpx.AsyncClient(headers={"User-Agent": cfg.settings.user_agent}, timeout=20.0) as client, Fetcher(cfg) as fetcher:
            router = SearchRouter.from_config(cfg, client=client, quota=quota, gemini=gemini)
            with Session(get_engine(), expire_on_commit=False) as session:
                try:
                    report = await run_resolve(session, cfg, gemini=gemini, embedder=embedder, router=router,
                                               fetcher=fetcher, find_websites=websites)
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
                finally:
                    if gemini is not None:
                        await gemini.aclose()
        return report

    r = asyncio.run(run())
    console.print(
        f"[bold]{r.discoveries} candidates[/bold] -> {r.records} records -> [bold]{r.companies} companies[/bold] "
        f"({r.companies_created} new, {r.companies_reused} existing, {r.companies_merged} merged) -> "
        f"[bold]{r.founders} founders[/bold] ({r.founders_created} new)"
    )
    console.print(f"pairs compared {r.pairs_compared}; LLM tiebreaks {r.llm_tiebreaks} (+{r.llm_reused} reused); "
                  f"websites found {r.websites_found}/{r.websites_tried}" + (f"; {'; '.join(r.notes)}" if r.notes else ""))
    table = Table(title=f"{top} largest merge clusters", show_lines=True)
    for col in ("Canonical", "Candidates", "Domain", "Aliases", "Scouts", "Merge evidence (pairs)"):
        table.add_column(col, overflow="fold")
    for cl in r.clusters[:top]:
        aliases = sorted(set(cl.raw_names) - {cl.name})
        scouts = sorted({s for rec in cl.records for s in rec.scouts})
        table.add_row(cl.name, str(len(cl.discovery_ids)), cl.domain or "-", ", ".join(aliases) or "-",
                      ", ".join(scouts), ", ".join(f"{m}x{n}" for m, n in cl.methods.most_common()) or "-")
    console.print(table)


def _ranked_companies(session: Any, top: int | None, names: list[str] | None = None) -> list[Any]:
    from screener.enrich.priority import rank_companies

    ranked = rank_companies(session, get_config().enrich["priority"])
    if names:  # --company: exactly these (by name, case-insensitive), in priority order
        from screener.resolve.normalize import normalize_name

        wanted = {normalize_name(n) for n in names}
        picked = [c for c, _ in ranked if normalize_name(c.name) in wanted]
        if missing := wanted - {normalize_name(c.name) for c in picked}:
            console.print(f"[yellow]no company record named: {', '.join(sorted(missing))}[/yellow]")
        return picked
    return [c for c, _ in (ranked[:top] if top else ranked)]


def _claim_status_table(session: Any, company_ids: list[Any] | None, title: str) -> None:
    from sqlalchemy import func, select

    from screener.models import Claim, CompanyFounder

    q = select(Claim.verified_status, func.count()).group_by(Claim.verified_status)
    if company_ids is not None:
        founder_ids = select(CompanyFounder.founder_id).where(CompanyFounder.company_id.in_(company_ids))
        q = q.where(((Claim.entity_type == "company") & Claim.entity_id.in_(company_ids))
                    | ((Claim.entity_type == "founder") & Claim.entity_id.in_(founder_ids)))
    table = Table(title=title)
    table.add_column("Status")
    table.add_column("Claims", justify="right")
    for status, n in session.execute(q).all():
        table.add_row(status, str(n))
    console.print(table)


@app.command()
def enrich(
    top: int = typer.Option(0, "--top", help="Only the top N companies by preliminary priority (0 = all)."),
    company: list[str] = typer.Option([], "--company", help="Only these companies, by name (repeatable)."),
) -> None:
    """Gather facts as grounded Claims for each company (verification is `screener verify`)."""
    from sqlalchemy.orm import Session, sessionmaker

    from screener.db import get_engine
    from screener.enrich.service import Enricher, load_targets
    from screener.http import Fetcher
    from screener.search import SearchRouter

    from screener.policy import effective_config

    engine = get_engine()
    with Session(engine) as s:
        cfg = effective_config(s, get_config())  # extraction vocabulary follows the active AI Policy
        companies = _ranked_companies(s, top or None, company)
        targets = load_targets(s, companies)
        ids = [c.id for c in companies]
    factory = sessionmaker(engine, expire_on_commit=False)

    async def run() -> Any:
        quota = _quota_store()
        gemini = _gemini()
        if gemini is None:
            raise typer.BadParameter("enrichment needs Gemini: set GEMINI_API_KEY and GEMINI_MODEL_FAST")
        async with httpx.AsyncClient(headers={"User-Agent": cfg.settings.user_agent}, timeout=20.0) as client, Fetcher(cfg) as fetcher:
            router = SearchRouter.from_config(cfg, client=client, quota=quota, gemini=gemini)
            try:
                return await Enricher(cfg, fetcher=fetcher, router=router, gemini=gemini, quota=quota).enrich(factory, targets)
            finally:
                await gemini.aclose()

    r = asyncio.run(run())
    console.print(f"[bold]{r.companies} companies[/bold]: {r.pages} pages, {r.llm_calls} extraction calls, "
                  f"{r.drafts} facts extracted, {r.rejected_grounding} rejected by grounding, [bold]{r.stored} new claims[/bold], "
                  f"{r.founders_created} founders added")
    console.print("by field: " + ", ".join(f"{k}={v}" for k, v in r.by_field.most_common()))
    if r.errors:
        console.print(f"{len(r.errors)} source errors (logged, run continued):", markup=False)
        for e in r.errors[:10]:
            console.print(f"  - {e}", markup=False)
    with Session(engine) as s:
        _claim_status_table(s, ids, "claims by status (these companies)")


@app.command()
def verify(
    top: int = typer.Option(0, "--top", help="Only the top N companies by preliminary priority (0 = all)."),
    reverify: bool = typer.Option(False, "--reverify", help="Re-check claims that were already verified."),
    company: list[str] = typer.Option([], "--company", help="Only these companies, by name (repeatable)."),
) -> None:
    """Verify claims independently (fresh fetch + separate verifier), then corroboration, liveness, identity."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.http import Fetcher
    from screener.verify.service import Verifier

    cfg = get_config()
    engine = get_engine()

    async def run() -> Any:
        gemini = _gemini()
        if gemini is None:
            raise typer.BadParameter("verification needs Gemini: set GEMINI_API_KEY and GEMINI_MODEL_FAST")
        async with Fetcher(cfg) as fetcher:
            with Session(engine, expire_on_commit=False) as s:
                companies = _ranked_companies(s, top or None, company)
                try:
                    return await Verifier(cfg, fetcher=fetcher, gemini=gemini).run(s, companies, reverify=reverify), [c.id for c in companies]
                finally:
                    await gemini.aclose()

    r, ids = asyncio.run(run())
    console.print(f"checked {r.claims_checked} claims from {r.sources} sources ({r.sources_unavailable} unavailable), "
                  f"{r.llm_calls} verifier calls, {r.grounding_failed} failed grounding on fresh fetch")
    console.print("this run: " + ", ".join(f"{k}={v}" for k, v in r.by_status.most_common()))
    console.print("corroboration (exits/funding/revenue): " + (", ".join(f"{k}={v}" for k, v in r.corroboration.items()) or "none"))
    console.print(f"rounds materialized {r.rounds}; liveness " + ", ".join(f"{k}={v}" for k, v in r.liveness.most_common())
                  + f"; founders identity-verified {r.identity_verified}/{r.founders}")
    if r.errors:
        console.print(f"[yellow]{len(r.errors)} errors[/yellow]; first: {r.errors[0]}", markup=False)
    with Session(engine) as s:
        _claim_status_table(s, ids, "claims by status (all verified companies)")


@app.command()
def dossier(
    n: int = typer.Option(5, "--n", help="Random companies to show."),
    company: str = typer.Option("", "--company", help="Show this company (name contains) instead."),
) -> None:
    """Print every supported claim (with its URL) for random or named companies."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.models import Claim, Company, CompanyFounder, Founder

    with Session(get_engine()) as s:
        supported = select(Claim.entity_id).where(Claim.verified_status == "supported", Claim.entity_type == "company")
        q = select(Company).where(Company.id.in_(supported))
        q = q.where(Company.name.ilike(f"%{company}%")) if company else q.order_by(func.random())
        for c in s.scalars(q.limit(n)).all():
            founders = s.execute(select(Founder).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
                                 .where(CompanyFounder.company_id == c.id)).scalars().all()
            names = {c.id: c.name} | {f.id: f.full_name for f in founders}
            claims = s.scalars(select(Claim).where(Claim.verified_status == "supported", Claim.entity_id.in_(list(names)))
                               .order_by(Claim.entity_type, Claim.field)).all()
            fl = ", ".join(f"{f.full_name}{' (identity verified)' if f.identity_verified else ''}" for f in founders) or "-"
            console.print(f"\n[bold]{c.name}[/bold]  {c.domain or '-'}  status={c.status} ({c.status_reason or '-'})\n"
                          f"founders: {fl}", markup=True)
            table = Table(show_lines=False)
            for col in ("Subject", "Field", "Value", "Conf", "Corrob.", "Source URL"):
                table.add_column(col, overflow="fold")
            for cl in claims:
                corr = (cl.meta or {}).get("corroboration", {})
                flag = "-" if not corr.get("needed") else ("yes" if corr.get("corroborated") else "NO")
                val = ", ".join(f"{k}={v}" for k, v in (cl.value_json or {}).items() if v not in (None, [], "")
                                and k not in ("absolute_original", "unit_original", "currency_original", "amount_original"))
                table.add_row(names.get(cl.entity_id, "?"), cl.field, val[:120],
                              f"{cl.verifier_confidence:.2f}" if cl.verifier_confidence is not None else "-", flag, cl.source_url)
            console.print(table)


@app.command()
def score(
    pairwise: bool = typer.Option(True, "--pairwise/--no-pairwise", help="Run the Gemini-smart pairwise judge."),
    top: int = typer.Option(25, "--top", help="Rows of the ranking to print."),
) -> None:
    """Score verified companies (SPEC 3), run the pairwise judge, select the MMR shortlist; persist with a run_id."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.embed import Embedder
    from screener.score.service import score as run_score

    cfg = get_config()

    async def run() -> Any:
        gemini = _gemini()
        try:
            with Session(get_engine(), expire_on_commit=False) as s:
                from screener.policy import effective_config

                pcfg = effective_config(s, cfg)  # the active AI Policy version
                return await run_score(s, pcfg, embedder=Embedder(pcfg, gemini=gemini), gemini=gemini, pairwise=pairwise)
        finally:
            if gemini is not None:
                await gemini.aclose()

    r = asyncio.run(run())
    passed = sum(1 for s in r.scored if s.gate.passed)
    eligible = sum(1 for s in r.scored if s.gate.shortlist_ok)
    console.print(f"run {r.run_id}: {len(r.scored)} companies scored, {passed} passed gates, {eligible} shortlist-eligible, "
                  f"shortlist {len(r.shortlist)}; pairwise {r.judgments} pairs x2 orders over top {r.pairwise_items}")
    table = Table(title=f"top {top} by Final'")
    for col in ("#", "Company", "Fit", "T", "I", "F", "C", "Final", "BT%", "Final'", "Gate", "Shortlist"):
        table.add_column(col, overflow="fold")
    for s in sorted(r.scored, key=lambda s: s.rank)[:top]:
        table.add_row(str(s.rank), s.cd.company.name, s.fit_type, f"{s.t:.2f}", f"{s.i:.2f}", f"{s.f:.2f}", f"{s.c:.2f}",
                      f"{s.final:.3f}", "-" if s.bt_pct is None else f"{s.bt_pct:.2f}", f"{s.final_prime:.3f}",
                      "pass" if s.gate.passed else s.gate.reason_code or "-", str(s.shortlist_rank or ""))
    console.print(table)
    ex = Table(title="top 5 exclusions (by would-have-ranked)")
    for col in ("Would rank", "Company", "Reason", "Explanation"):
        ex.add_column(col, overflow="fold")
    for s, code, text in sorted(r.exclusions, key=lambda e: e[0].rank)[:5]:
        ex.add_row(str(s.rank), s.cd.company.name, code, text)
    console.print(ex)


@app.command()
def calibrate() -> None:
    """recall@50 on config/seeds.yaml and Kendall-tau stability of the top 15 under +/-20% weight perturbation."""
    from datetime import datetime, timezone

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.embed import Embedder
    from screener.models import Company
    from screener.score.calibrate import recall_at, stability
    from screener.score.features import load
    from screener.score.gates import evaluate
    from screener.score.service import embed_companies
    from screener.scouts.prefilter import Prefilter

    cfg = get_config()
    conf = cfg.weights["calibration"]
    today = datetime.now(timezone.utc).date()

    async def run() -> Any:
        gemini = _gemini()
        try:
            with Session(get_engine(), expire_on_commit=False) as s:
                datas = load(s, cfg, list(s.scalars(select(Company).where(Company.verified_at.is_not(None)))), today)
                emb = await embed_companies(datas, Embedder(cfg, gemini=gemini))
                pf = Prefilter(cfg.sources.get("prefilter_keywords", []))
                min_sig = int(cfg.weights["shortlist_gate"]["min_verified_signal_claims"])
                eligible = {d.key for d in datas if evaluate(d, cfg.exclusions, pf, min_sig).passed}
                rep = stability(datas, cfg.weights, cfg.gap_map, emb, today, eligible, conf)
                recall = recall_at(s, cfg.seeds, int(conf["recall_at"]))
                return rep, recall, len(datas), len(eligible)
        finally:
            if gemini is not None:
                await gemini.aclose()

    rep, (recall, found, missing, neg_top), n, n_elig = asyncio.run(run())
    console.print(f"[bold]Stability[/bold] ({rep.trials} trials, weights x U(0.8, 1.2), top {rep.top_k} of {n_elig} "
                  f"gate-passing companies out of {n}; deterministic Final, judge not re-run)")
    for k, v in rep.summary.items():
        console.print(f"  {k}: {v:.3f}")
    if recall is None:
        console.print("[bold]recall@50[/bold]: not computable: config/seeds.yaml has no positives yet "
                      "(add real companies a reviewer agrees should rank highly).")
    else:
        console.print(f"[bold]recall@{conf['recall_at']}[/bold]: {recall:.2f} ({len(found)}/{len(found) + len(missing)}; "
                      f"missing: {', '.join(missing) or '-'})")
    console.print(f"[bold]seed negatives in the top {conf['recall_at']}[/bold]: {len(neg_top)}"
                  f"{' (' + ', '.join(neg_top) + ')' if neg_top else ''}")


def _emit(obj: Any) -> None:
    """Machine-readable result for the dashboard: one marked JSON line on stdout."""
    print("@@JSON@@" + json.dumps(obj, default=str), flush=True)


def _read_policy(file: Path | None) -> Any:
    from screener.policy import Policy

    raw = file.read_text(encoding="utf-8") if file else sys.stdin.read()
    return Policy.model_validate_json(raw)


@policy_app.command("show")
def policy_show() -> None:
    """The active policy (or the YAML default when none is saved) and the YAML default, as JSON."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.policy import active, default_policy

    cfg = get_config()
    with Session(get_engine()) as s:
        policy, version = active(s, cfg)
    _emit({"version": version, "policy": policy.model_dump(), "default": default_policy(cfg).model_dump()})


@policy_app.command("preview")
def policy_preview(file: Path = typer.Option(None, "--file", help="Draft policy JSON (default: stdin).")) -> None:
    """Shortlist under the current vs the draft policy, recomputed with no AI calls."""
    from pydantic import ValidationError
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.policy_preview import preview

    try:
        draft = _read_policy(file)
    except ValidationError as exc:
        _emit({"error": "invalid policy", "details": exc.errors(include_url=False)})
        raise typer.Exit(2) from exc
    async def run() -> Any:
        gemini = _gemini()  # cached embeddings only; preview never generates
        try:
            with Session(get_engine()) as s:
                return await preview(s, get_config(), draft, gemini)
        finally:
            if gemini is not None:
                await gemini.aclose()

    _emit(asyncio.run(run()))


@policy_app.command("save")
def policy_save(file: Path = typer.Option(None, "--file", help="Policy JSON (default: stdin)."),
                author: str = typer.Option("dashboard", "--author"), note: str = typer.Option("", "--note"),
                activate: bool = typer.Option(True, "--activate/--draft")) -> None:
    """Store a new policy version (active by default). Every score after this uses it."""
    from pydantic import ValidationError
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.policy import save

    try:
        policy = _read_policy(file)
    except ValidationError as exc:
        _emit({"error": "invalid policy", "details": exc.errors(include_url=False)})
        raise typer.Exit(2) from exc
    with Session(get_engine()) as s:
        version = save(s, get_config(), policy, author=author, note=note, activate=activate)
    _emit({"version": version, "active": activate})


@policy_app.command("activate")
def policy_activate(version: int = typer.Option(..., "--version")) -> None:
    """Make an earlier version active again (rollback)."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.policy import activate

    with Session(get_engine()) as s:
        activate(s, version)
    _emit({"version": version, "active": True})


@app.command()
def rescore(trigger: str = typer.Option("cli", "--trigger")) -> None:
    """Score + explain + seeds under the active AI Policy (no discovery or enrichment): a new published run."""
    from sqlalchemy.orm import Session

    from screener.config import CONFIG_DIR
    from screener.db import get_engine
    from screener.embed import Embedder
    from screener.explain.service import explain_run
    from screener.models import Run
    from screener.policy import effective_config
    from screener.score.seeds import refresh
    from screener.score.service import score as run_score

    cfg = get_config()

    async def run() -> Any:
        gemini = _gemini()
        try:
            with Session(get_engine(), expire_on_commit=False) as s:
                pcfg = effective_config(s, cfg)
                r = await run_score(s, pcfg, embedder=Embedder(pcfg, gemini=gemini), gemini=gemini, pairwise=gemini is not None)
                run_row = s.get(Run, r.run_id)
                run_row.trigger = trigger if trigger in ("cli", "dashboard", "schedule") else "cli"
                s.commit()
                e = await explain_run(s, pcfg, gemini, run_row)
                refresh(s, CONFIG_DIR / "seeds.yaml", cfg.seeds, cfg.weights["calibration"].get("seeds", {}), r.run_id)
                return r, e, _publish_quietly(s, run_row)
        finally:
            if gemini is not None:
                await gemini.aclose()

    r, e, sheet = asyncio.run(run())
    judge = "judge reused from the last run (Gemini unavailable)" if r.judge_reused else f"{r.judgments} judged pairs"
    console.print(f"[green]OK[/green] run {str(r.run_id)[:8]}: {len(r.scored)} scored, shortlist "
                  f"{len(r.shortlist)}, {judge}, {e.written} explained ({e.template} from claims); Sheet: {sheet}")
    _emit({"run_id": str(r.run_id), "shortlist": len(r.shortlist), "judge_reused": r.judge_reused, "sheet": sheet})


def _publish_quietly(session: Any, run: Any) -> str:
    """Publish a run to the Google Sheet when it is configured. Fail soft: the run stands even if the Sheet fails."""
    from screener.publish.service import publish as do_publish
    from screener.publish.sheets import SheetPublisher

    st = get_config().settings
    if not (st.google_service_account_json and st.google_sheet_id):
        return "not configured"
    try:
        report = do_publish(session, SheetPublisher.from_settings(st), run)
        return f"published ({report.counts.get('Shortlist', 0)} shortlisted)"
    except Exception as exc:  # noqa: BLE001 - logged and reported, never fatal
        logging.getLogger(__name__).exception("publishing to the Google Sheet failed")
        return f"publish failed: {type(exc).__name__}: {str(exc)[:160]}"


@audit_app.command("sample")
def audit_sample(n: int = typer.Option(30, "--n"), seed: int = typer.Option(7, "--seed"),
                 out: Path = typer.Option(Path("docs/audit/claims-sample.csv"), "--out")) -> None:
    """Reproducible random sample of supported claims from the latest shortlist, for a manual accuracy check."""
    from sqlalchemy.orm import Session

    from screener.audit import sample_claims, write_csv
    from screener.db import get_engine

    with Session(get_engine()) as s:
        rows = sample_claims(s, n, seed)
    write_csv(rows, out)
    console.print(f"[green]OK[/green] {len(rows)} claims across {len({r['field'] for r in rows})} fields -> {out}")


@founders_app.command("dedupe")
def founders_dedupe() -> None:
    """Merge duplicate founders of the same company (same name tokens in another order, or a contained name)."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.resolve.founders import merge_duplicate_founders

    with Session(get_engine()) as s:
        merges = merge_duplicate_founders(s)
        s.commit()
    for m in merges:
        console.print(f"merged [bold]{m.merged}[/bold] into [bold]{m.kept}[/bold]")
    console.print(f"[green]OK[/green] {len(merges)} duplicate founder record(s) merged")


@seeds_app.command("refresh")
def seeds_refresh(dry_run: bool = typer.Option(False, "--dry-run", help="Show the seeds without writing the file.")) -> None:
    """Recompute auto seeds from the latest scored run (reviewer entries are kept as they are)."""
    from sqlalchemy.orm import Session

    from screener.config import CONFIG_DIR
    from screener.db import get_engine
    from screener.score.seeds import ORIGIN, refresh

    cfg = get_config()
    with Session(get_engine()) as s:
        r = refresh(s, CONFIG_DIR / "seeds.yaml", cfg.seeds, cfg.weights["calibration"].get("seeds", {}), write=not dry_run)
    if r["run_id"] is None:
        console.print("[yellow]No scored run yet[/yellow]: run `screener score` or `screener run` first.")
        raise typer.Exit(1)
    for side in ("positives", "negatives"):
        t = Table(title=f"{side} (run {r['run_id'][:8]})", show_lines=False)
        for col in ("Company", "Origin", "Reason", "Source"):
            t.add_column(col, overflow="fold")
        for e in r["seeds"][side]:
            if isinstance(e, dict):
                t.add_row(str(e.get("company")), str(e.get("origin") or "reviewer"), str(e.get("reason") or ""), str(e.get("source_url") or ""))
            else:
                t.add_row(str(e), "reviewer", "", "")
        console.print(t)
    verb = "would write" if dry_run else "wrote"
    console.print(f"[green]OK[/green] {verb} {r['positives']} auto positives, {r['negatives']} auto negatives "
                  f"({r['manual']} reviewer entries kept; origin '{ORIGIN}' entries are replaced on every refresh)")


@universe_app.command("ai-triage")
def universe_ai_triage(batch: int = typer.Option(50, "--batch"), concurrency: int = typer.Option(12, "--concurrency"),
                       limit: int = typer.Option(0, "--limit", help="Max prospects (0 = all queued).")) -> None:
    """Gemini reads each queued registry row (name + registry fields only) and reorders the light-tier queue:
    likely edtech product companies first. A routing guess, never stored as a fact."""
    from sqlalchemy.orm import sessionmaker

    from screener.db import get_engine
    from screener.universe.ai_triage import triage

    gemini = _gemini()
    if gemini is None:
        raise typer.BadParameter("GEMINI_API_KEY is not set")

    async def run() -> Any:
        try:
            return await triage(sessionmaker(get_engine()), gemini, batch_size=batch, concurrency=concurrency, limit=limit or None)
        finally:
            await gemini.aclose()

    r = asyncio.run(run())
    console.print(f"[green]OK[/green] {r.prospects} prospects in {r.calls} Gemini calls ({r.errors} failed batches): {r.labels}")


@universe_app.command("load-mca")
def universe_load_mca(max_pages: int = typer.Option(0, "--max-pages", help="Pages per resource this call (0 = all).")) -> None:
    """Load MCA company master data from the data.gov.in API (resumable; needs DATA_GOV_IN_API_KEY)."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.http import Fetcher
    from screener.scouts.prefilter import Prefilter
    from screener.universe.records import is_education_nic, name_is_educational
    from screener.universe.store import load_mca

    cfg = get_config()
    edu, pf = cfg.universe["education"], Prefilter(cfg.sources.get("prefilter_keywords", []))

    def candidate(row: Any) -> bool:  # education NIC in the CIN, or education words in the registry name
        return is_education_nic(row.nic_code, edu["nic_prefixes"]) or name_is_educational(row.name, edu, pf)

    keep = candidate if cfg.universe["mca"].get("store_only_candidates", True) else None
    key = cfg.settings.data_gov_in_api_key
    if key is None:
        raise typer.BadParameter("DATA_GOV_IN_API_KEY is empty in .env")
    if not cfg.universe["mca"].get("resource_ids"):
        raise typer.BadParameter("config/universe.yaml mca.resource_ids is empty (copy them from data.gov.in)")

    async def run() -> Any:
        async with Fetcher(cfg) as f:
            with Session(get_engine()) as s:
                return await load_mca(s, f, key.get_secret_value(), cfg.universe, max_pages=max_pages or None, keep=keep)

    for resource, n in asyncio.run(run()).items():
        console.print(f"{resource}: {n} rows upserted")


@universe_app.command("load-dpiit")
def universe_load_dpiit(path: Path = typer.Option(None, "--path", help="CSV path (default: DPIIT_STARTUPS_CSV).")) -> None:
    """Load the DPIIT recognised-startups CSV (Dataful export)."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.universe.store import load_dpiit_csv

    cfg = get_config()
    path = path or cfg.settings.dpiit_startups_csv
    if not path or not Path(path).exists():
        raise typer.BadParameter("set DPIIT_STARTUPS_CSV in .env (or --path) to the downloaded CSV")
    with Session(get_engine()) as s:
        console.print(f"{load_dpiit_csv(s, Path(path), cfg.universe)} DPIIT rows upserted")


@universe_app.command("load-yc")
def universe_load_yc() -> None:
    """Load every education company from the public YC directory (yc-oss) into the universe."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.http import Fetcher
    from screener.universe.store import load_yc

    cfg = get_config()

    async def run() -> int:
        async with Fetcher(cfg) as f:
            with Session(get_engine()) as s:
                return await load_yc(s, f, cfg.sources)

    console.print(f"{asyncio.run(run())} YC education companies upserted")


@universe_app.command("build")
def universe_build() -> None:
    """Scouts -> registry, cross-validate DPIIT vs MCA, build prospects, triage in code, queue the light tier."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.scouts.prefilter import Prefilter
    from screener.universe.store import build_prospects, cross_validate_all, load_scouts

    cfg = get_config()
    with Session(get_engine()) as s:
        console.print(f"scout rows: {load_scouts(s)}")
        console.print(f"cross-validation: {cross_validate_all(s, cfg.universe['validation']) or 'no DPIIT rows yet'}")
        counts = build_prospects(s, cfg.universe, Prefilter(cfg.sources.get('prefilter_keywords', [])))
        console.print("prospects: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    universe_status()


@universe_app.command("status")
def universe_status() -> None:
    """Universe and queue counts."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.universe.store import queue_stats

    with Session(get_engine()) as s:
        reg = s.execute(sql("select source, validation_status, count(*) from registry_entities group by 1,2 order by 1,2")).all()
        pro = s.execute(sql("select stage, coalesce(verdict,'-'), count(*) from prospects group by 1,2 order by 1,2")).all()
        table = Table(title="universe")
        for col in ("Table", "Group", "Count"):
            table.add_column(col)
        for src, v, n in reg:
            table.add_row("registry", f"{src} / {v}", str(n))
        for st, v, n in pro:
            table.add_row("prospects", f"{st} / {v}", str(n))
        for k, st, n in queue_stats(s):
            table.add_row("tasks", f"{k} / {st}", str(n))
        console.print(table)


@app.command()
def worker(
    kinds: str = typer.Option("light,deep,verify", "--kinds", help="Task kinds to process."),
    max_tasks: int = typer.Option(0, "--max-tasks", help="Stop after N tasks (0 = run forever)."),
    until_empty: bool = typer.Option(False, "--until-empty", help="Stop when the queue is empty."),
) -> None:
    """Real-time worker: claims queued tasks by priority and commits each result immediately."""
    from sqlalchemy.orm import sessionmaker

    from screener.db import get_engine
    from screener.http import Fetcher
    from screener.search import SearchRouter
    from screener.universe.worker import Worker

    cfg = get_config()
    factory = sessionmaker(get_engine(), expire_on_commit=False)

    async def run() -> Any:
        quota = _quota_store()
        gemini = _gemini()
        if gemini is None:
            raise typer.BadParameter("the worker needs Gemini: set GEMINI_API_KEY and GEMINI_MODEL_FAST")
        # A long-lived worker isn't a "run": the shared daily/monthly USD caps govern it, not the per-run caps.
        gemini.max_calls, gemini.run_budget_usd = float("inf"), None
        async with httpx.AsyncClient(headers={"User-Agent": cfg.settings.user_agent}, timeout=20.0) as client, Fetcher(cfg) as f:
            router = SearchRouter.from_config(cfg, client=client, quota=quota, gemini=gemini)
            try:
                return await Worker(cfg, factory, fetcher=f, router=router, gemini=gemini, quota=quota).run(
                    [k.strip() for k in kinds.split(",") if k.strip()], max_tasks=max_tasks or None, until_empty=until_empty)
            finally:
                await gemini.aclose()

    st = asyncio.run(run())
    console.print(f"done {st.done}; light verdicts {st.verdicts}; failed {st.failed}; light LLM calls {st.llm_calls}")
    universe_status()


def _print_shortlist(session: Any, run_id: Any) -> None:
    from sqlalchemy import select

    from screener.models import Run
    from screener.publish.service import shortlist_records

    run = session.get(Run, run_id)
    rows = shortlist_records(session, run)
    table = Table(title=f"Shortlist ({len(rows)}) - run {run_id}", show_lines=True)
    for col in ("#", "Company", "Founder(s)", "Fit", "Final", "T", "I", "F", "C", "Why they made the cut",
                "How they fit upGrad", "Evidence"):
        table.add_column(col, overflow="fold")
    for r in rows:
        ev = "\n".join(x for x in (r["Evidence 1"], r["Evidence 2"], r["Evidence 3"]) if x)
        table.add_row(str(r["Rank"]), r["Company"], r["Founder(s)"] or "-", r["Fit type"] or "-", f"{r['Final']:.3f}",
                      f"{r['T']:.2f}", f"{r['I']:.2f}", f"{r['F']:.2f}", f"{r['Confidence']:.2f}",
                      r["Why they made the cut"] or "-", r["How they fit upGrad"] or "-", ev or "-")
    console.print(table)


@app.command()
def explain() -> None:
    """Write why/fit lines (verified claims only) and evidence for the latest run's shortlist."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.explain.service import explain_run

    cfg = get_config()

    async def run() -> Any:
        gemini = _gemini()
        try:
            with Session(get_engine(), expire_on_commit=False) as s:
                from screener.policy import effective_config

                return await explain_run(s, effective_config(s, cfg), gemini)
        finally:
            if gemini is not None:
                await gemini.aclose()

    r = asyncio.run(run())
    console.print(f"{r.written} written: {r.llm} by the LLM ({r.regenerated} after regeneration), {r.template} from claims (fallback)")
    from sqlalchemy.orm import Session as S2

    from screener.db import get_engine as ge
    with S2(ge()) as s:
        _print_shortlist(s, r.run_id)


@app.command()
def publish() -> None:
    """Publish the latest run to the Google Sheet (all 5 tabs)."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.publish.service import publish as do_publish
    from screener.publish.sheets import SheetPublisher

    cfg = get_config()
    pub = SheetPublisher.from_settings(cfg.settings)
    with Session(get_engine(), expire_on_commit=False) as s:
        r = do_publish(s, pub)
    console.print(f"published run {r.run_id}: " + ", ".join(f"{k}={v}" for k, v in r.counts.items())
                  + f"; reviewer feedback synced {r.feedback_synced}; {r.url}")


@app.command("run")
def run_pipeline(
    top: int = typer.Option(150, "--top", help="Companies to enrich + verify (by preliminary priority)."),
    limit_per_scout: int = typer.Option(30, "--limit-per-scout"),
    scouts: str = typer.Option("all", "--scouts"),
    publish: bool = typer.Option(True, "--publish/--no-publish"),
    websites: bool = typer.Option(True, "--websites/--no-websites"),
    pairwise: bool = typer.Option(True, "--pairwise/--no-pairwise"),
    trigger: str = typer.Option("cli", "--trigger", help="cli | dashboard | schedule"),
    incremental: bool = typer.Option(True, "--incremental/--full", help="Only re-fetch new or stale entities."),
    resume: str = typer.Option("", "--resume", help="Resume this run_id from its last completed stage."),
) -> None:
    """Full chain: discover -> resolve -> enrich -> verify -> score -> explain -> publish (one run_id, resumable)."""
    import uuid as _uuid
    from sqlalchemy.orm import Session, sessionmaker

    from screener.db import get_engine
    from screener.orchestrator import Pipeline

    cfg = get_config()
    factory = sessionmaker(get_engine(), expire_on_commit=False)

    async def go() -> Any:
        quota = _quota_store()
        gemini = _gemini()
        if gemini is None:
            raise typer.BadParameter("the pipeline needs Gemini: set GEMINI_API_KEY and GEMINI_MODEL_FAST")
        try:
            return await Pipeline(cfg, factory, gemini=gemini, quota=quota, progress=lambda m: console.print(m, markup=False)).run(
                top=top, limit_per_scout=limit_per_scout, scouts=scouts, publish=publish, websites=websites,
                pairwise=pairwise, incremental=incremental, trigger=trigger,
                resume=_uuid.UUID(resume) if resume else None)
        finally:
            await gemini.aclose()

    r = asyncio.run(go())
    title = f"run {r.run_id}: {r.status}" + (f" (resumed at {r.resumed_from})" if r.resumed_from else "")
    table = Table(title=title)
    for col in ("Stage", "Status", "Summary"):
        table.add_column(col, overflow="fold")
    for stage, cp in r.stages.items():
        table.add_row(stage, cp.get("status", "-"), cp.get("summary") or "-")
    console.print(table)
    if r.status != "succeeded":
        console.print(f"resume with: uv run screener run --resume {r.run_id}")
    with Session(get_engine()) as s:
        _print_shortlist(s, r.run_id)


@app.command()
def watch(
    interval: float = typer.Option(2.0, "--interval", help="Refresh every N seconds."),
    once: bool = typer.Option(False, "--once", help="Print one snapshot and exit."),
) -> None:
    """Live view of background work: external calls per provider, latest calls, run stage, claims, queue."""
    import time

    from rich.live import Live

    from screener.db import get_engine
    from screener.watch import snapshot

    cfg = get_config()
    log_path = cfg.settings.cache_dir / "logs" / "screener.log"
    engine = get_engine()
    if once:
        console.print(snapshot(engine, log_path))
        return
    with Live(snapshot(engine, log_path), console=console, refresh_per_second=4, screen=False) as live:
        try:
            while True:
                time.sleep(interval)
                live.update(snapshot(engine, log_path))
        except KeyboardInterrupt:
            pass


@discoveries_app.command("sample")
def discoveries_sample(
    n: int = typer.Option(10, "--n", help="How many random rows."),
    batch: str = typer.Option("latest", "--batch", help="'latest', 'all' or a batch id."),
) -> None:
    """Print random discoveries with their source URLs for spot-checking."""
    from sqlalchemy import func, select

    from screener.db import session_scope
    from screener.models import Discovery

    with session_scope() as s:
        q = select(Discovery)
        if batch == "latest":
            latest = s.execute(select(Discovery.batch_id).order_by(Discovery.created_at.desc()).limit(1)).scalar()
            q = q.where(Discovery.batch_id == latest)
        elif batch != "all":
            q = q.where(Discovery.batch_id == batch)
        rows = s.execute(q.order_by(func.random()).limit(n)).scalars().all()
    table = Table(title=f"{len(rows)} random discoveries ({batch})", show_lines=True)
    for col in ("Scout", "Company", "Person", "Snippet / evidence", "Source URL"):
        table.add_column(col, overflow="fold")
    for d in rows:
        table.add_row(d.scout, d.raw_company_name, d.raw_person_name or "-",
                      (d.evidence_span or d.snippet or "")[:200], d.source_url)
    console.print(table)


if __name__ == "__main__":
    app()


# --- reviewer picks ------------------------------------------------------------------------------------------------


def _reviewer_table(session: Any, picks: Any) -> Table:
    from sqlalchemy import select

    from screener.models import Company, Feedback
    from screener.reviewer import pick_companies

    linked = pick_companies(session, picks)
    table = Table(title=f"Reviewer picks ({picks.origin})", show_lines=False)
    for col in ("Pick", "Company record", "Website", "Enriched", "Verified", "Reviewer label"):
        table.add_column(col, overflow="fold")
    for p in picks.picks:
        cid = linked.get(p.company)
        c = session.get(Company, cid) if cid else None
        fb = session.scalar(select(Feedback).where(Feedback.entity_id == cid).order_by(Feedback.synced_at.desc())) if cid else None
        table.add_row(p.company, c.name if c else "[yellow]not resolved yet[/yellow]", (c.website if c else None) or "-",
                      "yes" if c and c.enriched_at else "no", "yes" if c and c.verified_at else "no", (fb.label if fb else None) or "-")
    return table


@reviewer_app.command("import")
def reviewer_import(
    resolve_now: bool = typer.Option(True, "--resolve/--no-resolve", help="Run resolve right after adding the discoveries."),
) -> None:
    """Add the picks as discoveries (scout 'reviewer'), resolve them, and write their reviewer label/note."""
    import uuid as _uuid

    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.reviewer import apply_feedback, discovery_items, load_picks
    from screener.scouts.base import persist

    picks = load_picks()
    items = discovery_items(picks)
    added = persist(items, _uuid.uuid4())
    console.print(f"{len(picks.picks)} picks -> {len(items)} discoveries ({added} new) from origin '{picks.origin}'")
    if resolve_now:
        resolve(websites=True, top=0)
    with Session(get_engine(), expire_on_commit=False) as session:
        r = apply_feedback(session, picks)
        session.commit()
        console.print(f"reviewer notes: {r.written} written; linked {len(r.linked)}/{len(picks.picks)}"
                      + (f"; kept the Sheet's own entry for {', '.join(r.kept_sheet)}" if r.kept_sheet else "")
                      + (f"; not resolved yet: {', '.join(r.unresolved)}" if r.unresolved else ""))
        console.print(_reviewer_table(session, picks))


@reviewer_app.command("status")
def reviewer_status() -> None:
    """Show each pick's company record, enrichment/verification state and reviewer label."""
    from sqlalchemy.orm import Session

    from screener.db import get_engine
    from screener.reviewer import load_picks

    with Session(get_engine()) as session:
        console.print(_reviewer_table(session, load_picks()))
