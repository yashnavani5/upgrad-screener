"""`screener verify`: independent verification of unverified claims, then corroboration, funding rounds,
liveness, founder identity and materialization of supported facts onto companies/founders."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from screener.config import AppConfig
from screener.enrich.pages import fetch_page, window_text
from screener.models import Claim, Company, CompanyAlias, CompanyFounder, Founder, Round
from screener.scouts.grounding import span_in_source
from screener.urls import host_of
from screener.verify.rules import (
    ClaimView, IdentityInput, LivenessInput, corroborate, equivalent, identity_verified, liveness, parse_date,
)
from screener.verify.verifier import statement, verify_batch

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class VerifyReport:
    claims_checked: int = 0
    sources: int = 0
    sources_unavailable: int = 0
    llm_calls: int = 0
    grounding_failed: int = 0
    by_status: Counter[str] = field(default_factory=Counter)
    corroboration: Counter[str] = field(default_factory=Counter)
    rounds: int = 0
    liveness: Counter[str] = field(default_factory=Counter)
    identity_verified: int = 0
    founders: int = 0
    sources_deferred: int = 0
    errors: list[str] = field(default_factory=list)


def _view(c: Claim) -> ClaimView:
    return ClaimView(c.id, c.entity_id, c.field, c.value_json or {}, c.source_url, c.verified_status, c.verifier_confidence)


class Verifier:
    def __init__(self, cfg: AppConfig, *, fetcher: Any, gemini: Any) -> None:
        self.cfg, self.fetcher, self.gemini = cfg, fetcher, gemini
        self.vconf = cfg.enrich["verification"]
        self.max_chars = int(cfg.enrich["limits"]["page_chars"])

    async def _source_text(self, claim: Claim) -> tuple[str | None, str]:
        mode = (claim.meta or {}).get("text_mode", "article")
        if mode == "snippet":
            # LinkedIn/Crunchbase/Tracxn are never fetched: verify against the stored search snippet.
            return (claim.meta or {}).get("snippet_text"), "stored search snippet (never fetched)"
        if mode == "api":
            try:
                raw = await self.fetcher.get_raw(claim.source_url, use_cache=False, provider="github",
                                                 headers={"Accept": "application/vnd.github+json"})
                return raw.text, "fresh API fetch"
            except Exception as exc:
                return None, f"source unavailable: {type(exc).__name__}"
        page = await fetch_page(self.fetcher, claim.source_url, "full" if mode == "full" else "article", fresh=True)
        return (page.text, "fresh fetch") if page else (None, "source unavailable on fresh fetch")

    async def verify_claims(self, session: Session, claims: list[Claim], subjects: dict[Any, str], report: VerifyReport,
                            max_sources: int | None = None, on_progress: Any = None) -> None:
        groups: dict[str, list[Claim]] = defaultdict(list)
        for c in claims:
            key = c.source_url if (c.meta or {}).get("text_mode") != "snippet" else f"{c.source_url}#{c.id}"
            groups[key].append(c)
        if max_sources is not None and len(groups) > max_sources:  # budget guard: the rest stay unverified
            report.sources_deferred = len(groups) - max_sources
            groups = dict(list(groups.items())[:max_sources])
        report.sources = len(groups)
        sem = asyncio.Semaphore(6)
        batch_size = int(self.vconf["batch_size"])
        threshold = int(self.vconf["grounding_threshold"])
        results: dict[Any, tuple[str, float | None, str]] = {}
        nonlocal_done = [0]
        saved: set[Any] = set()

        def save(group: list[Claim]) -> None:
            now = utcnow()
            for c in group:
                if c.id in results and c.id not in saved:
                    saved.add(c.id)
                    status, conf, reason = results[c.id]
                    c.verified_status, c.verifier_confidence, c.verifier_reason, c.verified_at = status, conf, reason[:1000], now
                    report.by_status[status] += 1
            session.commit()

        async def one(group: list[Claim]) -> None:
            async with sem:
                try:
                    text, how = await self._source_text(group[0])
                except Exception as exc:  # fail soft: one source never stops verification
                    report.errors.append(f"source {group[0].source_url}: {type(exc).__name__}")
                    text, how = None, f"source unavailable: {type(exc).__name__}"
            if not text:
                report.sources_unavailable += 1
                for c in group:
                    results[c.id] = ("not_found", None, how)
                save(group)
                return
            to_llm = []
            for c in group:
                # 1. Grounding (code): the span must still be on the freshly fetched page.
                if span_in_source(c.evidence_span, text, threshold):
                    to_llm.append(c)
                else:
                    report.grounding_failed += 1
                    results[c.id] = ("not_found", None, f"grounding failed: evidence not in {how}")
            for start in range(0, len(to_llm), batch_size):
                batch = to_llm[start : start + batch_size]
                # 2. Verifier sees only claim statements + page text (windowed around the claims' locations).
                page = window_text(text, [c.evidence_span for c in batch], self.max_chars)
                stmts = [statement(c.field, c.value_json or {}, subjects.get(c.entity_id, "The company")) for c in batch]
                try:
                    async with sem:
                        verdicts = await verify_batch(self.gemini, stmts, page)
                    report.llm_calls += 1
                except Exception as exc:
                    report.errors.append(f"verifier {group[0].source_url}: {type(exc).__name__}")
                    continue  # stays unverified; retried next run
                by_idx = {v.claim_id: v for v in verdicts}
                for i, c in enumerate(batch):
                    v = by_idx.get(i)
                    if v:
                        results[c.id] = (v.status, round(v.confidence, 3), f"{v.reason} [{how}]")
            save(group)  # commit this source's verdicts now: a crash later never loses finished work
            nonlocal_done[0] += 1
            if nonlocal_done[0] % 25 == 0 or nonlocal_done[0] == len(groups):
                log.warning("verify progress %d/%d sources", nonlocal_done[0], len(groups))
            if on_progress is not None and (nonlocal_done[0] % 5 == 0 or nonlocal_done[0] == len(groups)):
                try:
                    on_progress(nonlocal_done[0], len(groups))
                except Exception:  # a heartbeat failure must never stop verification
                    log.exception("verify heartbeat failed")

        await asyncio.gather(*(one(g) for g in groups.values()))
        report.claims_checked = len(results)
        session.commit()

    async def website_resolves(self, company: Company) -> bool | None:
        url = company.website or (f"https://{company.domain}/" if company.domain else None)
        if not url:
            return None
        # One failed fetch is not death: try the stated URL, the other www/apex host, then plain http.
        host = host_of(url)
        alt = host[4:] if host.startswith("www.") else f"www.{host}"
        seen: set[str] = set()
        for candidate in (url, f"https://{alt}/", f"http://{host}/"):
            if candidate in seen:
                continue
            seen.add(candidate)
            result = await self.fetcher.fetch(candidate, use_cache=False, allow_fallbacks=False)
            if result.outcome in ("blocked_by_robots", "blocked_by_policy"):
                return None  # we may not check; not evidence of death
            if result.ok and (result.status or 0) < 400:
                return True
        return False

    def post_process(self, session: Session, company: Company, resolves: bool | None, report: VerifyReport) -> None:
        founders = session.scalars(select(Founder).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
                                   .where(CompanyFounder.company_id == company.id)).all()
        founder_ids = [f.id for f in founders]
        claims = session.scalars(select(Claim).where(
            ((Claim.entity_type == "company") & (Claim.entity_id == company.id))
            | ((Claim.entity_type == "founder") & (Claim.entity_id.in_(founder_ids)))
        )).all()
        views = [_view(c) for c in claims]

        # 3. Corroboration
        corr = corroborate(views, company.domain, self.vconf)
        for c in claims:
            r = corr[c.id]
            c.meta = {**(c.meta or {}), "corroboration": {"needed": r.needed, "corroborated": r.corroborated,
                                                          "domains": list(r.domains), "primary": r.primary}}
            if r.needed and c.verified_status == "supported":
                report.corroboration["corroborated" if r.corroborated else "awaiting_corroboration"] += 1

        def usable(c: Claim) -> bool:
            return c.verified_status == "supported" and corr[c.id].corroborated

        company_claims = [c for c in claims if c.entity_type == "company" and usable(c)]

        # Funding rounds: one Round per group of equivalent corroborated claims.
        session.execute(delete(Round).where(Round.company_id == company.id))
        rounds = [c for c in company_claims if c.field == "funding_round"]
        used: set[Any] = set()
        total = 0.0
        tol = float(self.vconf["amount_tolerance"])
        for c in sorted(rounds, key=lambda x: (not corr[x.id].primary, -(x.verifier_confidence or 0))):
            if c.id in used:
                continue
            group = [o for o in rounds if o.id == c.id or equivalent(_view(c), _view(o), tol)]
            used |= {o.id for o in group}
            v = c.value_json or {}
            session.add(Round(id=uuid.uuid4(), company_id=company.id, round_type=v.get("stage"),
                              amount_usd=v.get("amount_usd"), announced_on=parse_date(v.get("date")),
                              investors=sorted({i for o in group for i in (o.value_json or {}).get("investors", [])}),
                              source_claim_id=c.id))
            total += v.get("amount_usd") or 0.0
            report.rounds += 1

        # 4. Liveness
        dates = [parse_date((c.value_json or {}).get("date")) for c in company_claims
                 if c.field in ("latest_news_date", "github_last_push", "funding_round")]
        # Open job listings seen on the company's own site are activity on the day they were seen.
        dates += [c.retrieved_at.date() for c in company_claims
                  if c.field == "open_roles" and ((c.value_json or {}).get("count") or 0) > 0 and c.retrieved_at]
        latest = max((d for d in dates if d), default=None)
        site_people = [c for c in claims if c.entity_type == "founder" and c.field == "current_role"
                       and c.verified_status == "supported" and (c.meta or {}).get("extraction") == "site"]
        founder_listed = True if site_people else None
        status, reason = liveness(LivenessInput(resolves, latest, founder_listed, date.today(),
                                                int(self.vconf["liveness_months"])))
        report.liveness[status] += 1

        # 5. Identity
        names = [company.name] + list(session.scalars(select(CompanyAlias.alias).where(CompanyAlias.company_id == company.id)))
        company_views = [_view(c) for c in claims if c.entity_type == "company"]
        for f in founders:
            fv = [_view(c) for c in claims if c.entity_type == "founder" and c.entity_id == f.id]
            f.identity_verified = identity_verified(IdentityInput(f.full_name, names, fv, company_views))
            report.identity_verified += f.identity_verified
            role = next((c for c in claims if c.entity_id == f.id and c.field == "current_role" and usable(c)), None)
            if role:
                f.current_title = ((role.value_json or {}).get("role") or "")[:300] or f.current_title
        report.founders += len(founders)

        # Materialize supported facts (claims stay the source of truth).
        def best(field_: str) -> dict[str, Any] | None:
            cands = [c for c in company_claims if c.field == field_]
            if not cands:
                return None
            cands.sort(key=lambda c: (c.source_type != "company_site", -(c.verifier_confidence or 0)))
            return cands[0].value_json or {}

        if v := best("description"):
            company.description = v.get("text")
        if v := best("city"):
            company.city = v.get("city")
        if v := best("country"):
            company.country = v.get("country")
        if v := best("founded_year"):
            company.founded_year = v.get("year")
        if v := best("legal_name"):
            company.legal_name = (v.get("name") or "")[:300] or None
        if v := best("open_roles"):
            company.open_roles = v.get("count")
        company.segment_tags = sorted({(c.value_json or {}).get("tag") for c in company_claims if c.field == "segment"} - {None})
        company.capability_tags = sorted({(c.value_json or {}).get("tag") for c in company_claims if c.field == "capability"} - {None})
        company.total_raised_usd = round(total, 2) if total else None
        company.last_activity_at = latest
        company.status, company.status_reason = status, reason
        company.verified_at = utcnow()

    async def run(self, session: Session, companies: list[Company], *, reverify: bool = False,
                  max_sources: int | None = None, on_progress: Any = None) -> VerifyReport:
        report = VerifyReport()
        ids = [c.id for c in companies]
        founder_rows = session.execute(select(CompanyFounder.founder_id, Founder.full_name)
                                       .join(Founder, Founder.id == CompanyFounder.founder_id)
                                       .where(CompanyFounder.company_id.in_(ids))).all()
        subjects: dict[Any, str] = {c.id: c.name for c in companies} | {fid: name for fid, name in founder_rows}
        q = select(Claim).where(
            ((Claim.entity_type == "company") & (Claim.entity_id.in_(ids)))
            | ((Claim.entity_type == "founder") & (Claim.entity_id.in_([f for f, _ in founder_rows])))
        )
        if not reverify:
            q = q.where(Claim.verified_status == "unverified")
        claims = session.scalars(q).all()
        await self.verify_claims(session, claims, subjects, report, max_sources, on_progress)
        session.commit()

        sem = asyncio.Semaphore(6)

        async def check(c: Company) -> tuple[Company, bool | None]:
            async with sem:
                try:
                    return c, await self.website_resolves(c)
                except Exception:
                    return c, None

        for company, resolves in await asyncio.gather(*(check(c) for c in companies)):
            try:
                self.post_process(session, company, resolves, report)
                session.commit()
            except Exception as exc:
                session.rollback()
                report.errors.append(f"{company.name}: post-process: {type(exc).__name__}: {str(exc)[:150]}")
        return report
