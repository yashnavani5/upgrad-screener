"""`screener enrich`: gather facts about each company as grounded Claims (verification is a separate step).

Per company: official site (home/about/team/careers), funding/acquisition news, revenue coverage, Tofler/Zauba
summaries (one polite fetch each), founder snippets (LinkedIn/Crunchbase/Tracxn: snippets only, never fetched)
and founder interviews, GitHub momentum. Every fact must quote an evidence_span found in the fetched text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.config import AppConfig
from screener.enrich.claims import ClaimDraft, grounded, store
from screener.enrich.money import to_usd, year_of
from screener.enrich.pages import Page, fetch_page, published_line, site_links
from screener.enrich.schemas import (
    BASE_RULES, FounderFacts, NewsFacts, RegistryFacts, RevenueFacts, SiteFacts, SnippetPeople, target_header,
)
from screener.models import Company, CompanyAlias, CompanyFounder, Discovery, Founder
from screener.resolve.normalize import is_listed_domain, normalize_person, registered_domain, same_person
from screener.scouts.grounding import name_in_text
from screener.urls import is_blocked_by_policy

log = logging.getLogger(__name__)

FOUNDER_ROLE = re.compile(r"\b(co[- ]?founder|founder|founding)\b", re.IGNORECASE)
NEWS_SCOUTS = ("news_rss", "gdelt")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Target:
    company_id: uuid.UUID
    name: str
    aliases: list[str]
    domain: str | None
    website: str | None
    founders: list[str]
    discoveries: list[Discovery]
    rank: int | None = None   # position in the priority order (PDL budget)

    def names(self) -> list[str]:
        return [self.name] + [a for a in self.aliases if a != self.name]

    def mentioned_in(self, text: str) -> bool:
        return any(name_in_text(n, text) for n in self.names())


@dataclass
class Extraction:
    kind: str                 # site | news | revenue | registry | snippets | founder_page | github
    source_url: str
    source_type: str
    text: str                 # the exact text the extractor saw (grounding reference)
    mode: str                 # full | article | snippet | api
    fetched_at: datetime
    data: Any
    title: str = ""
    published: str | None = None
    snippet_texts: dict[int, tuple[str, str]] = field(default_factory=dict)  # result_id -> (url, text)


@dataclass
class EnrichReport:
    companies: int = 0
    pages: int = 0
    llm_calls: int = 0
    drafts: int = 0
    rejected_grounding: int = 0
    stored: int = 0
    founders_created: int = 0
    skipped_budget: int = 0
    by_field: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


class Enricher:
    def __init__(self, cfg: AppConfig, *, fetcher: Any, router: Any, gemini: Any, quota: Any = None) -> None:
        self.cfg, self.fetcher, self.router, self.gemini, self.quota = cfg, fetcher, router, gemini, quota
        self.conf = cfg.enrich
        self.limits = self.conf["limits"]
        self.snippet_only = self.conf["snippet_only_domains"]
        gap = cfg.gap_map
        self.segments = {b["id"]: b["name"] for b in gap.get("business_units", [])}
        self.capabilities = {c["id"]: c["name"] for c in gap.get("wanted_capabilities", [])}
        self.model = cfg.settings.gemini_model_fast

    # --- LLM -------------------------------------------------------------------------------------

    async def _extract(self, schema: type[BaseModel], target: Target, page_label: str, text: str,
                       extra: str = "", report: EnrichReport | None = None) -> Any:
        prompt = (
            f"{target_header(target.name, target.aliases, target.domain, target.founders)}\n{extra}\n"
            f"{page_label}\nPAGE TEXT:\n{text[: int(self.limits['page_chars'])]}"
        )
        if report:
            report.llm_calls += 1
        return await self.gemini.generate_json(prompt, schema, "fast", system=BASE_RULES)

    # --- sources ---------------------------------------------------------------------------------

    def _usable(self, url: str, target: Target) -> bool:
        d = registered_domain(url)
        return bool(d) and not is_blocked_by_policy(url) and not is_listed_domain(d, self.snippet_only)

    async def _search(self, template: str, target: Target, n: int = 10) -> list[Any]:
        res = await self.router.search(template.format(name=target.name), strategy="cheap", n=n)
        return res.results

    async def _site(self, t: Target, report: EnrichReport) -> list[Extraction]:
        if not (t.website or t.domain):
            return []
        home = await fetch_page(self.fetcher, t.website or f"https://{t.domain}/", "full")
        if not home:
            return []
        pages: list[tuple[str, Page]] = [("home", home)]
        domain = t.domain or registered_domain(home.url)
        if domain:
            for kind, url in site_links(home, domain, self.conf["site_link_keywords"], int(self.limits["site_pages"]) - 1):
                if p := await fetch_page(self.fetcher, url, "full"):
                    pages.append((kind, p))
        vocab = (
            "Allowed segment ids (upGrad business units): " + json.dumps(self.segments)
            + "\nAllowed capability ids (upGrad wanted capabilities): " + json.dumps(self.capabilities)
        )
        out = []
        for kind, p in pages:
            report.pages += 1
            data = await self._extract(SiteFacts, t, f"PAGE TYPE: company {kind} page ({p.url})", p.text, vocab, report)
            out.append(Extraction("site", p.url, "company_site", p.text, "full", p.fetched_at, data, p.title))
            if kind == "team":
                fdata = await self._extract(FounderFacts, t, f"PAGE TYPE: company team page ({p.url})", p.text, "", report)
                out.append(Extraction("founder_page", p.url, "company_site", p.text, "full", p.fetched_at, fdata, p.title))
        return out

    async def _news(self, t: Target, report: EnrichReport) -> list[Extraction]:
        urls = [d.source_url for d in t.discoveries if d.scout in NEWS_SCOUTS]
        for r in await self._search(self.conf["searches"]["funding"], t):
            if t.mentioned_in(f"{r.title} {r.snippet}") and registered_domain(r.url) != t.domain:
                urls.append(r.url)
        out, seen = [], set()
        for url in urls:
            if len(out) >= int(self.limits["news_articles"]) or url in seen or not self._usable(url, t):
                continue
            seen.add(url)
            p = await fetch_page(self.fetcher, url, "article")
            if not p or not t.mentioned_in(p.text):
                continue
            report.pages += 1
            data = await self._extract(NewsFacts, t, f"PAGE TYPE: news article ({p.url})", p.text, "", report)
            out.append(Extraction("news", p.url, "press", p.text, "article", p.fetched_at, data, p.title, p.published))
        return out

    async def _revenue(self, t: Target, report: EnrichReport) -> list[Extraction]:
        out = []
        for r in await self._search(self.conf["searches"]["revenue"], t):
            if len(out) >= int(self.limits["revenue_pages"]):
                break
            if not self._usable(r.url, t) or not t.mentioned_in(f"{r.title} {r.snippet}"):
                continue
            if not re.search(r"revenue|fy\s?\d{2}|crore|loss|profit", f"{r.title} {r.snippet}", re.I):
                continue
            p = await fetch_page(self.fetcher, r.url, "article")
            if not p or not t.mentioned_in(p.text):
                continue
            report.pages += 1
            data = await self._extract(RevenueFacts, t, f"PAGE TYPE: financial news ({p.url})", p.text, "", report)
            out.append(Extraction("revenue", p.url, "press", p.text, "article", p.fetched_at, data, p.title, p.published))
        return out

    async def _registry(self, t: Target, report: EnrichReport) -> list[Extraction]:
        out, done = [], set()
        for r in await self._search(self.conf["searches"]["registry"], t):
            d = registered_domain(r.url)
            if d not in self.conf["registry_domains"] or d in done:
                continue
            done.add(d)  # one polite fetch per registry site
            p = await fetch_page(self.fetcher, r.url, "full")
            if not p or not t.mentioned_in(p.text):
                continue
            report.pages += 1
            data = await self._extract(RegistryFacts, t, f"PAGE TYPE: company registry summary ({p.url})", p.text, "", report)
            out.append(Extraction("registry", p.url, "other", p.text, "full", p.fetched_at, data, p.title))
        return out

    async def _founders(self, t: Target, report: EnrichReport) -> list[Extraction]:
        results = await self._search(self.conf["searches"]["founders"], t)
        out: list[Extraction] = []
        # Snippets must be the search engine's text; Gemini grounding "snippets" are the model's own summary.
        snippets = {i: (r.url, f"{r.title}\n{r.snippet}") for i, r in enumerate(results)
                    if r.provider != "gemini_grounding"
                    and is_listed_domain(registered_domain(r.url), self.snippet_only) and t.mentioned_in(f"{r.title} {r.snippet}")}
        if snippets:
            text = "\n\n".join(f"[result_id={i}] URL: {u}\n{s}" for i, (u, s) in snippets.items())
            data = await self._extract(SnippetPeople, t, "PAGE TYPE: search-engine result snippets (one per result_id)", text,
                                       "Extract founders of the TARGET company named in each snippet.", report)
            out.append(Extraction("snippets", "", "search_snippet", text, "snippet", utcnow(), data, snippet_texts=snippets))
        pages = 0
        for r in results:
            if pages >= int(self.limits["founder_pages"]):
                break
            if not self._usable(r.url, t) or registered_domain(r.url) == t.domain:
                continue
            p = await fetch_page(self.fetcher, r.url, "article")
            if not p or not t.mentioned_in(p.text):
                continue
            pages += 1
            report.pages += 1
            data = await self._extract(FounderFacts, t, f"PAGE TYPE: article/interview about the founders ({p.url})", p.text, "", report)
            out.append(Extraction("founder_page", p.url, "press", p.text, "article", p.fetched_at, data, p.title))
        return out

    async def _github(self, t: Target) -> list[Extraction]:
        repos = sorted({(d.extra or {}).get("repo") for d in t.discoveries if d.scout == "github"} - {None})
        out = []
        for repo in repos[:2]:
            url = f"https://api.github.com/repos/{repo}"
            try:
                raw = await self.fetcher.get_raw(url, provider="github", headers={"Accept": "application/vnd.github+json"})
            except Exception as exc:
                log.info("github %s: %s", repo, exc)
                continue
            out.append(Extraction("github", url, "github", raw.text, "api", utcnow(), json.loads(raw.text)))
        return out

    async def _pdl(self, t: Target, report: EnrichReport) -> list[Extraction]:
        """PDL person enrichment: only for the top-N by priority, within the monthly quota (free tier = 100).
        The stored API response is the verification source, so verifying never spends quota again."""
        key = self.cfg.settings.pdl_api_key
        pdl = self.conf["pdl"]
        if key is None or t.rank is None or t.rank >= int(pdl["top_n"]) or self.quota is None:
            return []
        out = []
        period = utcnow().strftime("%Y-%m")
        for name in t.founders[:2]:
            if not self.quota.try_consume("pdl", period, 1, float(pdl["monthly_limit"])):
                break
            params = {"name": name, "company": t.domain or t.name, "min_likelihood": 6}
            try:
                raw = await self.fetcher.get_raw(pdl["endpoint"], params=params, provider="pdl",
                                                 headers={"X-Api-Key": key.get_secret_value()}, use_cache=True)
            except Exception as exc:
                log.info("pdl %s: %s", name, exc)
                continue
            data = json.loads(raw.text).get("data") or {}
            url = f"{pdl['endpoint']}?name={name}&company={params['company']}"
            out.append(Extraction("pdl", url, "other", raw.text, "snippet", utcnow(), {"name": name, "data": data}))
        return out

    async def collect(self, t: Target, report: EnrichReport, out: list[Extraction] | None = None) -> list[Extraction]:
        """Runs every source with a per-step time limit. `out` is filled as steps finish, so a caller that
        times out the whole company still keeps what was collected."""
        out = out if out is not None else []
        step_timeout = float(self.limits.get("step_timeout_seconds", 180))

        async def github(t: Target, report: EnrichReport) -> list[Extraction]:
            return await self._github(t)

        for step in (self._site, self._news, self._revenue, self._registry, self._founders, self._pdl, github):
            try:
                out += await asyncio.wait_for(step(t, report), timeout=step_timeout)
            except Exception as exc:  # fail soft (incl. timeouts): one source never stops the company
                report.errors.append(f"{t.name}: {step.__name__}: {type(exc).__name__}: {str(exc)[:120]}")
        return out

    # --- extraction -> grounded claim drafts -----------------------------------------------------

    def drafts(self, session: Session, t: Target, extractions: list[Extraction], report: EnrichReport) -> list[ClaimDraft]:
        inr = float(self.conf["currency"]["inr_per_usd"])
        out: list[ClaimDraft] = []

        def add(entity_type: str, entity_id: uuid.UUID, field_: str, value: dict[str, Any], span: str | None,
                ex: Extraction, source_url: str | None = None, ref_text: str | None = None) -> None:
            if not span:
                return
            d = ClaimDraft(entity_type, entity_id, field_, value, source_url or ex.source_url, ex.source_type, span,
                           ex.fetched_at, self.model, {"extraction": ex.kind, "text_mode": ex.mode})
            if ex.mode == "snippet":  # never fetched: verification uses this stored snippet
                d.meta["snippet_text"] = ref_text if ref_text is not None else ex.text
            report.drafts += 1
            if not grounded(d, ref_text if ref_text is not None else ex.text, int(self.conf["verification"]["grounding_threshold"])):
                report.rejected_grounding += 1
                return
            report.by_field[field_] += 1
            out.append(d)

        cid = t.company_id
        seen_tags: set[tuple[str, str]] = set()  # one claim per segment/capability tag per company
        for ex in extractions:
            data = ex.data
            if ex.kind == "site":
                for fname, fact, key in (("description", data.description, "text"), ("city", data.city, "city"),
                                         ("country", data.country, "country"), ("headcount", data.headcount, "text"),
                                         ("legal_name", data.legal_name, "name")):
                    if fact and fact.value:
                        add("company", cid, fname, {key: fact.value}, fact.evidence_span, ex)
                if data.founded_year and data.founded_year.value:
                    add("company", cid, "founded_year", {"year": data.founded_year.value}, data.founded_year.evidence_span, ex)
                if data.open_roles and data.open_roles.value is not None:
                    add("company", cid, "open_roles", {"count": data.open_roles.value}, data.open_roles.evidence_span, ex)
                for tag in data.segments:
                    if tag.tag in self.segments and ("segment", tag.tag) not in seen_tags:
                        seen_tags.add(("segment", tag.tag))
                        add("company", cid, "segment", {"tag": tag.tag, "label": self.segments[tag.tag]}, tag.evidence_span, ex)
                for tag in data.capabilities:
                    if tag.tag in self.capabilities and ("capability", tag.tag) not in seen_tags:
                        seen_tags.add(("capability", tag.tag))
                        add("company", cid, "capability", {"tag": tag.tag, "label": self.capabilities[tag.tag]}, tag.evidence_span, ex)
                for person in data.people:
                    self._person_claim(session, t, person.name, person.role, person.evidence_span, ex, add, report)
            elif ex.kind == "news":
                if not data.about_target:
                    continue
                for rnd in data.rounds:
                    money = to_usd(rnd.amount, rnd.currency, rnd.unit, inr)
                    value = {"date": rnd.date, "stage": rnd.stage, "investors": rnd.investors, **(money or {})}
                    if money is None and rnd.amount is not None:
                        value.update({"amount_original": rnd.amount, "unit_original": rnd.unit, "currency_original": rnd.currency})
                    add("company", cid, "funding_round", value, rnd.evidence_span, ex)
                if data.acquired_by:
                    add("company", cid, "acquired", {"acquirer": data.acquired_by.acquirer, "date": data.acquired_by.date},
                        data.acquired_by.evidence_span, ex)
                for person in data.people:
                    self._person_claim(session, t, person.name, person.role, person.evidence_span, ex, add, report)
                if ex.published and ex.title:
                    add("company", cid, "latest_news_date", {"date": ex.published[:10], "title": ex.title},
                        published_line(ex.published), ex)
            elif ex.kind == "size":
                if not data.about_target:
                    continue
                if (tf := data.total_funding) and (money := to_usd(tf.amount, tf.currency, tf.unit, inr)):
                    add("company", cid, "total_funding", {**money, "as_of": tf.as_of}, tf.evidence_span, ex)
                if data.headcount and data.headcount.value:
                    add("company", cid, "headcount", {"text": data.headcount.value}, data.headcount.evidence_span, ex)
                for rnd in data.rounds:
                    money = to_usd(rnd.amount, rnd.currency, rnd.unit, inr)
                    value = {"date": rnd.date, "stage": rnd.stage, "investors": rnd.investors, **(money or {})}
                    add("company", cid, "funding_round", value, rnd.evidence_span, ex)
            elif ex.kind == "location":
                if not data.about_target:
                    continue
                for fname, fact in (("city", data.city), ("country", data.country)):
                    if fact and fact.value:
                        add("company", cid, fname, {fname: fact.value}, fact.evidence_span, ex)
            elif ex.kind == "revenue":
                for rev in data.revenues:
                    money = to_usd(rev.amount, rev.currency, rev.unit, inr)
                    value = {"fiscal_year": rev.fiscal_year, **(money or {"amount_original": rev.amount,
                             "unit_original": rev.unit, "currency_original": rev.currency})}
                    add("company", cid, "revenue", value, rev.evidence_span, ex)
            elif ex.kind == "registry":
                if data.legal_name and data.legal_name.value:
                    add("company", cid, "legal_name", {"name": data.legal_name.value}, data.legal_name.evidence_span, ex)
                if data.incorporation_date and data.incorporation_date.value:
                    add("company", cid, "incorporation_date", {"date": data.incorporation_date.value},
                        data.incorporation_date.evidence_span, ex)
                for director in data.directors:
                    add("company", cid, "director", {"name": director.name}, director.evidence_span, ex)
            elif ex.kind == "snippets":
                for person in data.people:
                    url, text = ex.snippet_texts.get(person.result_id, (None, None))
                    if url and text:
                        self._person_claim(session, t, person.name, person.role, person.evidence_span, ex, add, report,
                                           source_url=url, ref_text=text)
            elif ex.kind == "founder_page":
                for prof in data.founders:
                    founder = self._founder(session, t, prof.name, prof.current_role, report)
                    if founder is None:
                        continue
                    if prof.current_role and prof.current_role_span:
                        add("founder", founder.id, "current_role", {"company": t.name, "role": prof.current_role},
                            prof.current_role_span, ex)
                    for job in prof.careers:
                        add("founder", founder.id, "prior_role", {"company": job.company, "role": job.role,
                            "start_year": job.start_year, "end_year": job.end_year}, job.evidence_span, ex)
                        if job.exit:
                            add("founder", founder.id, "prior_exit", {"company": job.company, "acquirer": job.acquirer,
                                "year": job.end_year}, job.evidence_span, ex)
                    for edu in prof.education:
                        add("founder", founder.id, "education", {"institution": edu.institution, "degree": edu.degree,
                            "year": edu.year}, edu.evidence_span, ex)
            elif ex.kind == "pdl":
                founder = self._founder(session, t, data["name"], None, report)
                if founder is None:
                    continue
                for job in data["data"].get("experience") or []:
                    company = (job.get("company") or {}).get("name")
                    m = re.search(r'"name"\s*:\s*"' + re.escape(company) + '"', ex.text) if company else None
                    if m:
                        add("founder", founder.id, "prior_role", {"company": company, "role": (job.get("title") or {}).get("name"),
                            "start_year": parse_year(job.get("start_date")), "end_year": parse_year(job.get("end_date"))},
                            m.group(0), ex, ref_text=ex.text)
                for edu in data["data"].get("education") or []:
                    school = (edu.get("school") or {}).get("name")
                    m = re.search(r'"name"\s*:\s*"' + re.escape(school) + '"', ex.text) if school else None
                    if m:
                        add("founder", founder.id, "education", {"institution": school, "degree": (edu.get("degrees") or [None])[0],
                            "year": parse_year(edu.get("end_date"))}, m.group(0), ex, ref_text=ex.text)
            elif ex.kind == "github":
                for key, fname, value in (("stargazers_count", "github_stars", lambda v: {"repo": data.get("full_name"), "stars": v}),
                                          ("pushed_at", "github_last_push", lambda v: {"repo": data.get("full_name"), "date": str(v)[:10]})):
                    m = re.search(rf'"{key}"\s*:\s*("[^"]*"|\d+)', ex.text)
                    if m and data.get(key) is not None:
                        add("company", cid, fname, value(data[key]), m.group(0), ex)
        return out

    def _founder(self, session: Session, t: Target, name: str, role: str | None, report: EnrichReport) -> Founder | None:
        """Existing founder of this company by normalized name, or a new one if the role says founder."""
        norm = normalize_person(name)
        if len(norm.split()) < 2:
            return None
        existing = next((f for f in session.scalars(
            select(Founder).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
            .where(CompanyFounder.company_id == t.company_id)) if same_person(f.normalized_name or f.full_name, norm)), None)
        if existing or not (role and FOUNDER_ROLE.search(role)):
            return existing
        founder = Founder(id=uuid.uuid4(), full_name=name.strip()[:300], normalized_name=norm[:300], other_urls=[])
        session.add(founder)
        session.flush()  # no ORM relationship: the founder row must exist before the link row
        session.add(CompanyFounder(id=uuid.uuid4(), company_id=t.company_id, founder_id=founder.id, role=(role or "founder")[:200], is_current=True))
        session.flush()
        report.founders_created += 1
        t.founders.append(founder.full_name)
        return founder

    def _person_claim(self, session: Session, t: Target, name: str, role: str | None, span: str, ex: Extraction,
                      add: Any, report: EnrichReport, source_url: str | None = None, ref_text: str | None = None) -> None:
        founder = self._founder(session, t, name, role, report)
        if founder is not None and role:
            add("founder", founder.id, "current_role", {"company": t.name, "role": role}, span, ex, source_url, ref_text)

    # --- run -------------------------------------------------------------------------------------

    async def enrich(self, session_factory: Any, targets: list[Target], *, should_stop: Any = None,
                     on_progress: Any = None) -> EnrichReport:
        """`should_stop()`: checked before each company (budget guard) - the company is left for a later run.
        `on_progress(done, total)`: called after each company is stored (run heartbeat)."""
        report = EnrichReport(companies=len(targets))
        sem = asyncio.Semaphore(int(self.limits["concurrency"]))

        company_timeout = float(self.limits.get("company_timeout_seconds", 600))
        done = 0

        async def one(t: Target) -> None:
            nonlocal done
            extractions: list[Extraction] = []
            async with sem:
                if should_stop is not None and should_stop():
                    report.errors.append(f"{t.name}: not enriched (budget guard)")
                    report.skipped_budget += 1
                    return
                try:
                    await asyncio.wait_for(self.collect(t, report, extractions), timeout=company_timeout)
                except Exception as exc:  # timeout: keep whatever was collected
                    report.errors.append(f"{t.name}: collect: {type(exc).__name__} after {company_timeout:.0f}s (partial kept)")
            with session_factory() as session:
                try:
                    drafts = self.drafts(session, t, extractions, report)
                    report.stored += store(session, drafts)
                    company = session.get(Company, t.company_id)
                    if company is not None:
                        company.enriched_at = utcnow()
                    session.commit()
                except Exception as exc:
                    session.rollback()
                    report.errors.append(f"{t.name}: store: {type(exc).__name__}: {str(exc)[:150]}")
            done += 1
            log.warning("enrich progress %d/%d: %s (%d extractions)", done, len(targets), t.name, len(extractions))
            if on_progress is not None:
                try:
                    on_progress(done, len(targets))
                except Exception:
                    pass

        await asyncio.gather(*(one(t) for t in targets))
        return report


def parse_year(text: str | None) -> int | None:
    return year_of(text)


def load_targets(session: Session, companies: list[Company]) -> list[Target]:
    targets = []
    for rank, c in enumerate(companies):
        aliases = list(session.scalars(select(CompanyAlias.alias).where(CompanyAlias.company_id == c.id)))
        founders = list(session.scalars(
            select(Founder.full_name).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
            .where(CompanyFounder.company_id == c.id)
        ))
        discoveries = list(session.scalars(select(Discovery).where(Discovery.company_id == c.id)))
        targets.append(Target(c.id, c.name, aliases, c.domain, c.website, founders, discoveries, rank))
    return targets
