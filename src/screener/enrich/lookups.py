"""Targeted lookups for the top-ranked companies, only where data is missing (gaps from the 2026-10-25 review):

- size:      total funding / headcount / rounds, so an unknown size no longer passes the size gate silently
- location:  city / country, so India-first scoring has evidence
- founder:   prior roles, exits and education per founder (interviews, profiles; LinkedIn via snippets only)
- X:         founder X profiles (handle + bio) and company posts (funding, launches, hiring), from search-engine
             snippets only: x.com's robots.txt disallows automated access (incl. oEmbed) and the API is paid

Each lookup is recorded in companies.lookups / founders.lookups (ISO date) and is never paid twice. Facts go
through the same grounded ClaimDraft path as enrichment; the verify stage checks them like any other claim.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from screener.enrich.claims import store
from screener.enrich.pages import fetch_page
from screener.enrich.schemas import FounderFacts, LocationFacts, NewsFacts, SizeFacts
from screener.enrich.service import (
    Enricher,
    EnrichReport,
    Extraction,
    Target,
    load_targets,
)
from screener.models import Company, Founder, Score
from screener.resolve.normalize import is_listed_domain, registered_domain, same_person
from screener.scouts.grounding import name_in_text
from screener.scouts.x import is_x, parse_x_profile, profile_handle

log = logging.getLogger(__name__)

BACKGROUND_FIELDS = ("prior_role", "prior_exit", "education")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LookupReport:
    companies: int = 0
    size: int = 0
    location: int = 0
    founders: int = 0
    pages: int = 0
    llm_calls: int = 0
    stored: int = 0
    x_profiles: int = 0
    errors: list[str] = field(default_factory=list)


def lookup_targets(session: Session, cfg: Any, top_n: int, ranked_fallback: list[Company]) -> list[Company]:
    """Top companies of the latest scored run (closest to the shortlist), else the enrichment priority order."""
    from screener.score.seeds import scored_run_id

    run_id = scored_run_id(session)
    if run_id is None:
        return ranked_fallback[:top_n]
    from screener.models import Candidate

    rows = session.execute(select(Company).join(Candidate, Candidate.company_id == Company.id)
                           .join(Score, Score.candidate_id == Candidate.id)
                           .where(Score.run_id == run_id).order_by(Score.rank).limit(top_n)).scalars().all()
    return list(rows) or ranked_fallback[:top_n]


def background_key(version: int) -> str:
    """founders.lookups key of a background lookup version (v1 kept its original name)."""
    return "background" if version <= 1 else f"background_v{version}"


def needs(cd: Any, founders_per_company: int, background_version: int = 1,
          min_background: int = 1) -> tuple[list[str], list[Founder], list[Founder]]:
    """(company lookups still needed, founders needing a background lookup, founders needing an X lookup).
    Pure on CompanyData. The company X lookup (posts about funding/launches/hiring) runs once per company.
    A founder gets a background lookup once per `background_version` while they have fewer than
    `min_background` verified background facts (prior roles, exits, education); a version is never paid twice."""
    done = cd.company.lookups or {}
    todo = [k for k, missing in (("size", not cd.size_known), ("location", cd.geo == "unknown"), ("x", True))
            if missing and k not in done]
    top = cd.founders[:founders_per_company]
    key = background_key(background_version)
    background = [fd.founder for fd in top if key not in (fd.founder.lookups or {})
                  and sum(1 for c in fd.claims if c.field in BACKGROUND_FIELDS) < min_background]
    x = [fd.founder for fd in top if "x" not in (fd.founder.lookups or {})]
    return todo, background, x


class Lookups:
    def __init__(self, enricher: Enricher) -> None:
        self.e = enricher
        self.conf = enricher.conf["lookups"]
        self.queries = self.conf["queries"]
        self.per_query = int(self.conf.get("pages_per_query", 2))

    async def _pages(self, t: Target, query: str, must_mention: list[str], report: LookupReport) -> list[Any]:
        """Fetched, non-snippet pages from one search that mention every name in `must_mention`."""
        out, seen = [], set()
        results = (await self.e.router.search(query, strategy="cheap", n=10)).results if query else []
        for r in results:
            if len(out) >= self.per_query or r.url in seen or not self.e._usable(r.url, t):
                continue
            seen.add(r.url)
            if not all(name_in_text(n, f"{r.title} {r.snippet}") for n in must_mention[:1]):
                continue  # the result itself must mention the target before we spend a fetch
            p = await fetch_page(self.e.fetcher, r.url, "article")
            if p and all(name_in_text(n, p.text) for n in must_mention):
                report.pages += 1
                out.append(p)
        return out

    async def _company(self, t: Target, kind: str, schema: Any, label: str, report: LookupReport) -> list[Extraction]:
        out = []
        for q in self.queries[kind]:
            for p in await self._pages(t, q.format(name=t.name), [t.name], report):
                report.llm_calls += 1
                data = await self.e._extract(schema, t, f"PAGE TYPE: {label} ({p.url})", p.text)
                out.append(Extraction(kind, p.url, "press", p.text, "article", p.fetched_at, data, p.title, p.published))
        return out

    async def _founder(self, t: Target, name: str, report: LookupReport) -> list[Extraction]:
        only = f"Extract ONLY the founder named {name} (a founder of the target company); ignore everyone else."
        out = []
        for q in self.queries["founder"]:
            query = q.format(name=t.name, founder=name)
            results = (await self.e.router.search(query, strategy="cheap", n=10)).results
            # LinkedIn/Crunchbase/Tracxn: snippet only (never fetched), and it must name both founder and company.
            for r in [r for r in results if r.provider != "gemini_grounding"  # model summary, not source text
                      and is_listed_domain(registered_domain(r.url), self.e.snippet_only)][: self.per_query]:
                text = f"{r.title}\n{r.snippet}"
                if name_in_text(name, text) and t.mentioned_in(text):
                    report.llm_calls += 1
                    data = await self.e._extract(FounderFacts, t, "PAGE TYPE: search-engine result snippet", text, only)
                    out.append(Extraction("founder_page", r.url, "search_snippet", text, "snippet", utcnow(), data, r.title))
            for p in await self._pages(t, query, [name, t.name], report):
                report.llm_calls += 1
                data = await self.e._extract(FounderFacts, t, f"PAGE TYPE: article/profile about a founder ({p.url})", p.text, only)
                out.append(Extraction("founder_page", p.url, "press", p.text, "article", p.fetched_at, data, p.title))
        return out

    async def _founder_x(self, t: Target, name: str, handles: dict[str, str], key: str,
                         report: LookupReport) -> list[Extraction]:
        """The founder's X profile from search results (never fetched): handle + bio facts (roles, education)."""
        only = f"Extract ONLY the founder named {name} (a founder of the target company); ignore everyone else."
        for q in self.queries.get("founder_x", []):
            for r in (await self.e.router.search(q.format(name=t.name, founder=name), strategy="cheap", n=10)).results:
                if r.provider == "gemini_grounding":
                    continue  # model summary, not source text
                handle = profile_handle(r.url)
                parsed = parse_x_profile(r.title, r.snippet) if handle else None
                text = f"{r.title}\n{r.snippet}"
                # Same person AND the bio names the company: a common name alone is not enough.
                if not parsed or not same_person(parsed["name"] or "", name) or not t.mentioned_in(text):
                    continue
                handles[key] = f"https://x.com/{handle}"
                report.llm_calls += 1
                data = await self.e._extract(FounderFacts, t, "PAGE TYPE: X profile search-result snippet (bio)", text, only)
                return [Extraction("founder_page", f"https://x.com/{handle}", "search_snippet", text, "snippet", utcnow(),
                                   data, r.title)]
        return []

    async def _company_x(self, t: Target, report: LookupReport) -> list[Extraction]:
        """X posts naming the company (funding, launches, hiring), from search snippets only."""
        out = []
        for q in self.queries.get("company_x", []):
            results = (await self.e.router.search(q.format(name=t.name), strategy="cheap", n=10)).results
            for r in [r for r in results if is_x(r.url) and r.provider != "gemini_grounding"
                      and t.mentioned_in(f"{r.title} {r.snippet}")][: self.per_query]:
                text = f"{r.title}\n{r.snippet}"
                report.llm_calls += 1
                data = await self.e._extract(NewsFacts, t, "PAGE TYPE: X post search-result snippet", text)
                out.append(Extraction("news", r.url, "search_snippet", text, "snippet", utcnow(), data, r.title))
        return out

    async def run(self, session_factory: Any, cds: list[Any], *, should_stop: Any = None,
                  on_progress: Any = None) -> LookupReport:
        """`on_progress(done, total)`: called after each company (run heartbeat, so the run is never seen as stale)."""
        report = LookupReport()
        done = 0
        n_founders = int(self.conf.get("founders_per_company", 2))
        bg_version = int(self.conf.get("founder_background_version", 1))
        bg_min = int(self.conf.get("founder_background_min_facts", 1))
        sem = asyncio.Semaphore(int(self.e.limits["concurrency"]))
        step_timeout = float(self.e.limits.get("step_timeout_seconds", 180))

        async def one(cd: Any) -> None:
            todo, founders, x_founders = needs(cd, n_founders, bg_version, bg_min)
            if not todo and not founders and not x_founders:
                return
            handles: dict[str, str] = {}
            async with sem:
                if should_stop is not None and should_stop():
                    return
                with session_factory() as s:
                    t = load_targets(s, [s.get(Company, cd.company.id)])[0]
                report.companies += 1
                extractions: list[Extraction] = []
                steps: list[tuple[str, Any]] = []
                if "size" in todo:
                    steps.append(("size", self._company(t, "size", SizeFacts, "page about the company's funding/size", report)))
                if "location" in todo:
                    steps.append(("location", self._company(t, "location", LocationFacts, "page about the company", report)))
                if "x" in todo:
                    steps.append(("x", self._company_x(t, report)))
                steps += [(f"founder:{f.id}", self._founder(t, f.full_name, report)) for f in founders]
                steps += [(f"x:{f.id}", self._founder_x(t, f.full_name, handles, f"x:{f.id}", report)) for f in x_founders]
                finished: set[str] = set()
                for key, coro in steps:
                    try:
                        extractions += await asyncio.wait_for(coro, timeout=step_timeout)
                        finished.add(key)
                    except Exception as exc:  # fail soft: a failed lookup is retried on a later run
                        report.errors.append(f"{t.name}: {key}: {type(exc).__name__}: {str(exc)[:120]}")
            with session_factory() as s:
                try:
                    er = EnrichReport()
                    report.stored += store(s, self.e.drafts(s, t, extractions, er))
                    today = utcnow().date().isoformat()
                    company = s.get(Company, cd.company.id)
                    company.lookups = {**(company.lookups or {}), **{k: today for k in todo if k in finished}}
                    flag_modified(company, "lookups")
                    for f in founders:
                        if f"founder:{f.id}" in finished:
                            row = s.get(Founder, f.id)
                            row.lookups = {**(row.lookups or {}), "background": today, background_key(bg_version): today}
                            flag_modified(row, "lookups")
                    for f in x_founders:
                        if f"x:{f.id}" in finished:
                            row = s.get(Founder, f.id)
                            row.lookups = {**(row.lookups or {}), "x": today}
                            if url := handles.get(f"x:{f.id}"):  # identity URL only; X never verifies alone
                                row.other_urls = sorted(set(row.other_urls or []) | {url})
                            flag_modified(row, "lookups")
                    s.commit()
                    report.size += "size" in finished
                    report.location += "location" in finished
                    report.founders += sum(1 for f in founders if f"founder:{f.id}" in finished)
                    report.x_profiles += len(handles)
                except Exception as exc:
                    s.rollback()
                    report.errors.append(f"{t.name}: store: {type(exc).__name__}: {str(exc)[:150]}")
            log.warning("lookups %s: %s + %d founder(s) + %d X profile(s), %d extraction(s)", t.name,
                        "/".join(todo) or "-", len(founders), len(handles), len(extractions))

        async def tracked(cd: Any) -> None:
            nonlocal done
            try:
                await one(cd)
            finally:
                done += 1
                if on_progress is not None:
                    try:
                        on_progress(done, len(cds))
                    except Exception:  # a heartbeat failure never stops the lookups
                        log.exception("lookups heartbeat failed")

        await asyncio.gather(*(tracked(cd) for cd in cds))
        return report
