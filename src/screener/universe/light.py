"""Light tier: one homepage fetch + one Gemini-fast call per prospect. The verdict (fit / not_fit / unknown) is
stored on the prospect, so the same company is never crawled or classified again unless its registry data
changes or `light.refresh_days` passes. Fit companies are promoted into `companies` for the deep tier."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.enrich.claims import ClaimDraft, grounded, store
from screener.enrich.pages import fetch_page
from screener.enrich.schemas import REQUIRED
from screener.models import Claim, Company, CompanyAlias, Prospect, RegistryEntity
from screener.resolve.normalize import normalize_name, registered_domain
from screener.resolve.website import find_website, verify_homepage

log = logging.getLogger(__name__)

SYSTEM = (
    "You decide from a company's own homepage whether it is an EDUCATION business: edtech, online or offline "
    "learning, tutoring, test prep, upskilling/skilling, schools/universities software, corporate learning, "
    f"study abroad, or education services. {REQUIRED} evidence_span must be an exact quote from the homepage "
    "that shows what the company does. Use only the page text."
)


class LightVerdict(BaseModel):
    is_education: bool = Field(description="True only if the homepage shows an education business.")
    is_edtech: bool = Field(description="True if it is technology-led education (platform, app, software).")
    description: str | None = Field(None, description="One sentence: what the company does, from the page.")
    segments: list[str] = Field(default_factory=list, description="Ids from the allowed upGrad business units.")
    capabilities: list[str] = Field(default_factory=list, description="Ids from the allowed wanted capabilities.")
    evidence_span: str | None = Field(None, description="Exact quote from the page that states what the company does.")


@dataclass
class LightOutcome:
    verdict: str              # fit | not_fit | unknown
    reason: str
    company_id: uuid.UUID | None = None
    llm_calls: int = 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def classify(prospect: Prospect, registry: list[RegistryEntity], *, cfg: Any, fetcher: Any, router: Any,
                   gemini: Any, quota: Any, session: Session | None = None, session_factory: Any = None) -> LightOutcome:
    """`session`: an open session (tests), or `session_factory`: a session is opened only to promote a fit, so no
    database connection is held during the 10-20 s of web lookups (lets one worker run many checks at once)."""
    universe, rconf = cfg.universe, cfg.resolve
    names = [prospect.name] + sorted({r.name for r in registry if r.name != prospect.name}) + [
        a for r in registry for a in (r.raw or {}).get("_aliases", [])]

    # 1. Website: registry/scout value validated against the homepage, else one gentle free search.
    match_url, span_site = None, None
    if prospect.website:
        page = await fetch_page(fetcher, prospect.website, "full", fallbacks=bool(universe["light"].get("browser_fallback", True)))
        if page and (span_site := verify_homepage(names, registered_domain(page.url) or "", page.title, page.text,
                                                  rconf.get("trailing_suffixes", []))):
            match_url, home = page.url, page
    if match_url is None:
        found = await find_website(router, fetcher, names[:3], shared=rconf.get("shared_domains", []),
                                   non_company=rconf.get("non_company_domains", []),
                                   suffixes=rconf.get("trailing_suffixes", []),
                                   results_considered=int(rconf.get("website_lookup", {}).get("results_considered", 5)),
                                   strategy=str(universe["light"].get("website_strategy", "cheap")),
                                   fallbacks=bool(universe["light"].get("browser_fallback", True)))
        if found is None:
            return LightOutcome("unknown", "no verifiable website found")
        match_url, span_site = found.homepage, found.evidence_span
        home = await fetch_page(fetcher, found.homepage, "full", fallbacks=bool(universe["light"].get("browser_fallback", True)))
        if home is None:
            return LightOutcome("unknown", "website found but homepage could not be fetched")

    # 2. One Gemini-fast classification (daily budget), grounded on the homepage text.
    day = utcnow().strftime("%Y-%m-%d")
    if quota is not None and not quota.try_consume("gemini_light", day, 1, float(universe["light"]["daily_llm_budget"])):
        raise BudgetExhausted("daily light-tier LLM budget reached")
    gap = cfg.gap_map
    vocab = json.dumps({"business_units": {b["id"]: b["name"] for b in gap.get("business_units", [])},
                        "wanted_capabilities": {c["id"]: c["name"] for c in gap.get("wanted_capabilities", [])}})
    facts = "; ".join(x for x in {r.activity or "" for r in registry} | {r.sector or "" for r in registry} if x)[:500]
    prompt = (f"COMPANY: {prospect.name}" + (f" (registry: {facts})" if facts else "") + f"\nALLOWED IDS: {vocab}\n"
              f"HOMEPAGE ({home.url}):\n{home.text[: int(cfg.enrich['limits']['page_chars'])]}")
    verdict = await gemini.generate_json(prompt, LightVerdict, "fast", system=SYSTEM)

    draft_span = ClaimDraft("company", uuid.uuid4(), "description", {}, home.url, "company_site",
                            verdict.evidence_span or "", home.fetched_at)
    if verdict.is_education and not grounded(draft_span, home.text):
        return LightOutcome("unknown", "classifier evidence not found on the homepage (rejected)", llm_calls=1)
    if not verdict.is_education:
        return LightOutcome("not_fit", f"homepage does not show an education business: {verdict.description or ''}"[:500], llm_calls=1)

    if session is not None:
        company = promote(session, prospect, names, home, match_url, span_site, verdict, gap)
    else:
        with session_factory() as s2:
            company = promote(s2, prospect, names, home, match_url, span_site, verdict, gap)
            s2.commit()
    return LightOutcome("fit", verdict.description or "education business", company.id, llm_calls=1)


class BudgetExhausted(RuntimeError):
    pass


def promote(session: Session, prospect: Prospect, names: list[str], home: Any, match_url: str, span_site: str | None,
            verdict: LightVerdict, gap: dict[str, Any]) -> Company:
    """Create (or reuse by domain) the canonical company and record the light-tier facts as claims."""
    domain = registered_domain(match_url)
    company = session.scalar(select(Company).where(Company.domain == domain)) if domain else None
    if company is None:
        company = Company(id=uuid.uuid4(), name=prospect.name[:300], normalized_name=normalize_name(prospect.name)[:300],
                          domain=domain, website=match_url, status="unknown")
        session.add(company)
        session.flush()
    for alias in sorted(set(names)):
        if not session.scalar(select(CompanyAlias.id).where(CompanyAlias.company_id == company.id, CompanyAlias.alias == alias[:300])):
            session.add(CompanyAlias(id=uuid.uuid4(), company_id=company.id, alias=alias[:300], normalized=normalize_name(alias)[:300]))
    units = {b["id"]: b["name"] for b in gap.get("business_units", [])}
    wanted = {c["id"]: c["name"] for c in gap.get("wanted_capabilities", [])}
    drafts = []
    if span_site and not session.scalar(select(Claim.id).where(Claim.entity_id == company.id, Claim.field == "website")):
        drafts.append(ClaimDraft("company", company.id, "website", {"url": match_url, "domain": domain}, home.url,
                                 "company_site", span_site, home.fetched_at, None, {"extraction": "light", "text_mode": "full"}))
    if verdict.description and verdict.evidence_span:
        drafts.append(ClaimDraft("company", company.id, "description", {"text": verdict.description}, home.url,
                                 "company_site", verdict.evidence_span, home.fetched_at, None, {"extraction": "light", "text_mode": "full"}))
    for tag in verdict.segments:
        if tag in units and verdict.evidence_span:
            drafts.append(ClaimDraft("company", company.id, "segment", {"tag": tag, "label": units[tag]}, home.url,
                                     "company_site", verdict.evidence_span, home.fetched_at, None, {"extraction": "light", "text_mode": "full"}))
    for tag in verdict.capabilities:
        if tag in wanted and verdict.evidence_span:
            drafts.append(ClaimDraft("company", company.id, "capability", {"tag": tag, "label": wanted[tag]}, home.url,
                                     "company_site", verdict.evidence_span, home.fetched_at, None, {"extraction": "light", "text_mode": "full"}))
    store(session, [d for d in drafts if grounded(d, home.text)])
    session.flush()
    return company


def already_classified(prospect: Prospect, now: datetime | None = None) -> bool:
    """A stored verdict is reused (no crawl, no LLM) while the registry data is unchanged and it is fresh."""
    now = now or utcnow()
    return bool(prospect.verdict and prospect.processed_hash == prospect.content_hash
                and prospect.next_check_at and prospect.next_check_at > now)


def record(session: Session, prospect: Prospect, outcome: LightOutcome, refresh_days: int) -> None:
    """Persist the verdict immediately; it is reused instead of re-crawling."""
    prospect.verdict, prospect.verdict_reason = outcome.verdict, outcome.reason[:1000]
    prospect.llm_calls = (prospect.llm_calls or 0) + outcome.llm_calls
    prospect.processed_hash, prospect.processed_at = prospect.content_hash, utcnow()
    prospect.next_check_at = utcnow() + timedelta(days=refresh_days)
    prospect.stage = "promoted" if outcome.verdict == "fit" else "light_done"
    if outcome.company_id:
        prospect.company_id = outcome.company_id
