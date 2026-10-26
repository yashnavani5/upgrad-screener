"""`screener resolve`: discoveries -> canonical companies and founders, idempotently.

Every run re-clusters all discoveries, then maps clusters onto existing companies through the stored
discovery links and domains, so re-running never creates duplicates. A cluster that spans two existing
companies merges them (all references repointed). LLM tiebreak decisions are stored and reused.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from screener.config import AppConfig
from screener.models import (
    Candidate, Claim, Company, CompanyAlias, CompanyFounder, Discovery, Feedback, Founder, ResolutionPair, Round,
)
from screener.resolve.matching import (
    DomainUnionFind, PairFeatures, Record, candidate_pairs, canonical_name, decide, features, pair_key, record_key,
)
from screener.resolve.normalize import (
    company_domain, domain_label, is_listed_domain, looks_like_domain, looks_like_person, normalize_name,
    normalize_person, person_hard_key, registered_domain, surname,
)

log = logging.getLogger(__name__)

METHOD_PRIORITY = {"domain": 0, "rule:name+founder": 1, "rule:name+description": 2, "llm": 3, "website": 4}
MIN_DESCRIPTION_CHARS = 20


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Cluster:
    records: list[Record]
    domain: str | None
    name: str = ""
    norm: str = ""
    website: str | None = None
    website_match: Any = None  # WebsiteMatch | None
    methods: Counter[str] = field(default_factory=Counter)
    company_id: uuid.UUID | None = None

    @property
    def discovery_ids(self) -> list[Any]:
        return [d for r in self.records for d in r.discovery_ids]

    @property
    def raw_names(self) -> list[str]:
        return [n for r in self.records for n in r.raw_names]


@dataclass
class ResolveReport:
    discoveries: int = 0
    records: int = 0
    pairs_compared: int = 0
    llm_tiebreaks: int = 0
    llm_reused: int = 0
    websites_tried: int = 0
    websites_found: int = 0
    companies: int = 0
    companies_created: int = 0
    companies_reused: int = 0
    companies_merged: int = 0
    founders: int = 0
    founders_created: int = 0
    clusters: list[Cluster] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --- records -----------------------------------------------------------------------------------------


def build_records(discoveries: list[Discovery], conf: dict[str, Any]) -> list[Record]:
    suffixes, shared = conf.get("trailing_suffixes", []), conf.get("shared_domains", [])
    groups: dict[str, Record] = {}
    for d in discoveries:
        norm = normalize_name(d.raw_company_name, suffixes)
        if not norm:
            continue
        domain = company_domain(d.company_url, shared)
        website = d.company_url if domain else None
        from_name = False
        if domain is None and looks_like_domain(d.raw_company_name):
            named = registered_domain(d.raw_company_name)
            if named and not is_listed_domain(named, shared):
                domain, website, from_name = named, f"https://{named}/", True
        key = record_key(d.scout, d.source_url, norm)
        r = groups.get(key)
        if r is None:
            r = groups[key] = Record(key=key, name=d.raw_company_name, norm=norm, domain=domain, website=website,
                                     domain_from_name=from_name)
        elif r.domain is None and domain:
            r.domain, r.website, r.domain_from_name = domain, website, from_name
        r.discovery_ids.append(d.id)
        r.raw_names.append(d.raw_company_name.strip())
        r.scouts.add(d.scout)
        r.sources.add(d.source_url)
        if d.raw_person_name and looks_like_person(d.raw_person_name):
            r.founders.add(normalize_person(d.raw_person_name))
            if s := surname(d.raw_person_name):
                r.surnames.add(s)
        locations = (d.extra or {}).get("all_locations") or ""
        if not r.city and locations:
            r.city = locations.split(",")[0].strip().lower() or None
        text = d.snippet or d.evidence_span or ""
        if len(text) > len(r.description):
            r.description = text
    for r in groups.values():
        r.name = Counter(r.raw_names).most_common(1)[0][0]
    return list(groups.values())


# --- clustering --------------------------------------------------------------------------------------


async def _embeddings(records: list[Record], idx: set[int], embedder: Any) -> dict[int, list[float]]:
    usable = sorted(i for i in idx if len(records[i].description) >= MIN_DESCRIPTION_CHARS)
    if not usable or embedder is None:
        return {}
    result = await embedder.embed([records[i].description for i in usable], task="SEMANTIC_SIMILARITY")
    return dict(zip(usable, result.vectors, strict=True))


async def cluster(
    records: list[Record], conf: dict[str, Any], *, gemini: Any, embedder: Any,
    stored: dict[str, tuple[str, str, str | None]], report: ResolveReport,
) -> tuple[DomainUnionFind, list[dict[str, Any]]]:
    """Returns the union-find over records and the pair decisions to store."""
    thresholds = conf["thresholds"]
    pairs = candidate_pairs(records, int(conf.get("blocking", {}).get("name_prefix_chars", 4)))
    report.pairs_compared = len(pairs)
    embs = await _embeddings(records, {i for p in pairs for i in p}, embedder)
    merges: list[tuple[int, int, int, str]] = []
    to_store: list[dict[str, Any]] = []
    ambiguous: list[tuple[int, int, PairFeatures, str]] = []

    for i, j in pairs:
        f = features(records[i], records[j], embs.get(i), embs.get(j))
        decision, method = decide(f, thresholds)
        if decision == "merge":
            merges.append((METHOD_PRIORITY.get(method, 9), i, j, method))
            to_store.append(_pair_row(records[i], records[j], "merge", method, None, f))
        elif decision == "ambiguous":
            ambiguous.append((i, j, f, method))
        elif method == "domain_conflict" and f.jw >= float(thresholds["jw_ambiguous"]):
            to_store.append(_pair_row(records[i], records[j], "separate", method, "different domains", f))

    sem = asyncio.Semaphore(8)

    async def settle(i: int, j: int, f: PairFeatures, why: str) -> tuple[int, int, bool, str, str]:
        key = pair_key(records[i], records[j])
        if key in stored and stored[key][1] == "llm":
            report.llm_reused += 1
            return i, j, stored[key][0] == "merge", stored[key][2] or "", "stored"
        if gemini is None:
            return i, j, False, "no LLM configured for tiebreak", "no_llm"
        from screener.resolve.tiebreak import tiebreak

        async with sem:
            try:
                ans = await tiebreak(gemini, records[i], records[j], f)
            except Exception as exc:  # fail soft: no merge without a decision
                return i, j, False, f"tiebreak failed: {type(exc).__name__}", "error"
        report.llm_tiebreaks += 1
        return i, j, ans.same, f"{why}: {ans.reason}", "llm"

    for i, j, same, reason, how in await asyncio.gather(*(settle(*a) for a in ambiguous)):
        f = next(a[2] for a in ambiguous if a[0] == i and a[1] == j)
        if how in ("llm", "stored"):
            to_store.append(_pair_row(records[i], records[j], "merge" if same else "separate", "llm", reason, f))
        if same:
            merges.append((METHOD_PRIORITY["llm"], i, j, "llm"))

    uf = DomainUnionFind(records)
    for _, i, j, method in sorted(merges):
        if not uf.union(i, j):
            to_store.append(_pair_row(records[i], records[j], "separate", "domain_guard",
                                      f"{method} merge blocked: clusters have different domains", None))
    return uf, to_store


def _pair_row(a: Record, b: Record, decision: str, method: str, reason: str | None, f: PairFeatures | None) -> dict[str, Any]:
    return {"pair_key": pair_key(a, b), "left_name": a.name[:300], "right_name": b.name[:300], "decision": decision,
            "method": method, "reason": reason, "features": f.as_dict() if f else {}}


def make_clusters(records: list[Record], uf: DomainUnionFind, conf: dict[str, Any]) -> list[Cluster]:
    out = []
    for root, members in uf.groups().items():
        recs = [records[i] for i in members]
        domain = uf.domain[root]
        cl = Cluster(records=recs, domain=domain)
        cl.website = next((r.website for r in recs if r.domain == domain and r.website and not r.domain_from_name), None) \
            or next((r.website for r in recs if r.domain == domain and r.website), None)
        _finalize_name(cl, conf)
        out.append(cl)
    return out


def _finalize_name(cl: Cluster, conf: dict[str, Any]) -> None:
    cl.name = canonical_name(cl.records, cl.domain, domain_label)
    cl.norm = normalize_name(cl.name, conf.get("trailing_suffixes", []))


# --- website lookup ----------------------------------------------------------------------------------


async def lookup_websites(clusters: list[Cluster], conf: dict[str, Any], *, router: Any, fetcher: Any,
                          report: ResolveReport) -> None:
    from screener.resolve.website import find_website

    wl = conf.get("website_lookup", {})
    todo = [c for c in clusters if not c.domain][: int(wl.get("max_lookups", 400))]
    report.websites_tried = len(todo)
    sem = asyncio.Semaphore(int(wl.get("concurrency", 6)))

    async def one(cl: Cluster) -> None:
        names = [n for n, _ in Counter(cl.raw_names).most_common()]
        names = [cl.name] + [n for n in names if n != cl.name]
        async with sem:
            try:
                match = await find_website(
                    router, fetcher, names[:3], shared=conf.get("shared_domains", []),
                    non_company=conf.get("non_company_domains", []), suffixes=conf.get("trailing_suffixes", []),
                    results_considered=int(wl.get("results_considered", 5)),
                )
            except Exception as exc:  # fail soft
                log.warning("website lookup failed for %s: %s", cl.name, exc)
                return
        if match:
            cl.domain, cl.website, cl.website_match = match.domain, match.homepage, match
            report.websites_found += 1

    await asyncio.gather(*(one(c) for c in todo))


def merge_by_domain(clusters: list[Cluster], conf: dict[str, Any]) -> list[Cluster]:
    """After website lookup, clusters that now share a domain are the same company."""
    by_domain: dict[str, Cluster] = {}
    out: list[Cluster] = []
    for cl in clusters:
        if cl.domain and cl.domain in by_domain:
            keep = by_domain[cl.domain]
            keep.records += cl.records
            keep.methods["website"] += 1
            keep.website = keep.website or cl.website
            keep.website_match = keep.website_match or cl.website_match
            _finalize_name(keep, conf)
            continue
        if cl.domain:
            by_domain[cl.domain] = cl
        out.append(cl)
    return out


# --- persistence -------------------------------------------------------------------------------------


def _insert(session: Session):
    return pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert


def merge_companies(session: Session, keep: Company, drop: Company) -> None:
    """Repoint everything from `drop` to `keep`, then delete `drop`."""
    session.execute(update(Discovery).where(Discovery.company_id == drop.id).values(company_id=keep.id))
    keep_founders = set(session.scalars(select(CompanyFounder.founder_id).where(CompanyFounder.company_id == keep.id)))
    for cf in session.scalars(select(CompanyFounder).where(CompanyFounder.company_id == drop.id)).all():
        if cf.founder_id in keep_founders:
            session.delete(cf)
        else:
            cf.company_id = keep.id
    session.execute(update(Claim).where(Claim.entity_type == "company", Claim.entity_id == drop.id).values(entity_id=keep.id))
    keep_runs = set(session.scalars(select(Candidate.run_id).where(Candidate.company_id == keep.id)))
    for cand in session.scalars(select(Candidate).where(Candidate.company_id == drop.id)).all():
        if cand.run_id in keep_runs:
            session.delete(cand)
        else:
            cand.company_id = keep.id
    session.execute(update(Round).where(Round.company_id == drop.id).values(company_id=keep.id))
    session.execute(update(Feedback).where(Feedback.entity_id == drop.id).values(entity_id=keep.id))
    keep_aliases = set(session.scalars(select(CompanyAlias.alias).where(CompanyAlias.company_id == keep.id)))
    for alias in session.scalars(select(CompanyAlias).where(CompanyAlias.company_id == drop.id)).all():
        if alias.alias in keep_aliases:
            session.delete(alias)
        else:
            alias.company_id = keep.id
    keep.domain = keep.domain or drop.domain
    keep.website = keep.website or drop.website
    session.flush()
    session.delete(drop)
    session.flush()


def persist_companies(session: Session, clusters: list[Cluster], conf: dict[str, Any], report: ResolveReport) -> None:
    now = utcnow()
    discoveries = {d.id: d for d in session.scalars(select(Discovery)).all()}
    insert = _insert(session)
    used: set[uuid.UUID] = set()  # an existing company is claimed by at most one cluster per run
    for cl in clusters:
        ids = cl.discovery_ids
        linked = {discoveries[i].company_id for i in ids if discoveries[i].company_id}
        if cl.domain:
            linked |= set(session.scalars(select(Company.id).where(Company.domain == cl.domain)))
        existing = session.scalars(select(Company).where(Company.id.in_(linked)).order_by(Company.created_at)).all() if linked else []
        # Never merge two existing companies that have different domains.
        existing = [c for c in existing if not (cl.domain and c.domain and c.domain != cl.domain) and c.id not in used]
        if existing:
            company = existing[0]
            for other in existing[1:]:
                merge_companies(session, company, other)
                report.companies_merged += 1
            report.companies_reused += 1
        else:
            company = Company(id=uuid.uuid4(), name=cl.name, status="unknown")
            session.add(company)
            report.companies_created += 1
        company.name = cl.name[:300]
        company.normalized_name = cl.norm[:300]
        if cl.domain:
            company.domain = cl.domain
            company.website = company.website or cl.website
        session.flush()
        cl.company_id = company.id
        used.add(company.id)
        aliases = sorted(set(cl.raw_names))
        session.execute(
            insert(CompanyAlias.__table__).values([
                {"id": uuid.uuid4(), "company_id": company.id, "alias": a[:300],
                 "normalized": normalize_name(a, conf.get("trailing_suffixes", []))[:300], "created_at": now}
                for a in aliases
            ]).on_conflict_do_nothing(index_elements=["company_id", "alias"])
        )
        session.execute(update(Discovery).where(Discovery.id.in_(ids)).values(company_id=company.id, resolved_at=now))
        if cl.website_match is not None:
            _website_claim(session, company, cl.website_match)
    report.companies = len(clusters)


def _website_claim(session: Session, company: Company, match: Any) -> None:
    """The website found by search is a fact: store it as a Claim with its homepage evidence (rule 1)."""
    exists = session.scalar(select(Claim.id).where(
        Claim.entity_type == "company", Claim.entity_id == company.id, Claim.field == "website",
        Claim.source_url == match.homepage,
    ))
    if exists:
        return
    session.add(Claim(
        entity_type="company", entity_id=company.id, field="website",
        value_json={"url": match.homepage, "domain": match.domain}, source_url=match.homepage,
        source_type="company_site", retrieved_at=datetime.fromisoformat(match.retrieved_at),
        evidence_span=match.evidence_span, extractor_model=None, extract_confidence=None,
        verified_status="supported", verifier_confidence=None,
    ))


@dataclass
class _Mention:
    discovery: Discovery
    norm: str
    raw: str
    company_id: uuid.UUID
    hard_key: str | None
    role: str | None


def persist_founders(session: Session, report: ResolveReport) -> None:
    """Founders keyed on LinkedIn/GitHub/X URLs (hard keys), then (normalized name, company)."""
    now = utcnow()
    mentions: list[_Mention] = []
    for d in session.scalars(select(Discovery).where(Discovery.raw_person_name.is_not(None), Discovery.company_id.is_not(None))).all():
        key = person_hard_key(d.person_url)
        if not looks_like_person(d.raw_person_name):
            continue  # GitHub handles etc. are not person names
        role = (d.extra or {}).get("headline") or "founder"
        mentions.append(_Mention(d, normalize_person(d.raw_person_name), d.raw_person_name.strip(), d.company_id, key, role))

    parent = list(range(len(mentions)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[Any, int] = {}
    for i, m in enumerate(mentions):
        for k in filter(None, [("hard", m.hard_key) if m.hard_key else None, ("name", m.norm, m.company_id)]):
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    groups: dict[int, list[_Mention]] = defaultdict(list)
    for i, m in enumerate(mentions):
        groups[find(i)].append(m)

    founders = session.scalars(select(Founder)).all()
    by_url: dict[str, Founder] = {}
    for f in founders:
        for u in [f.linkedin_url, *(f.other_urls or [])]:
            if key := person_hard_key(u):
                by_url[key] = f
    links = session.execute(select(CompanyFounder.company_id, CompanyFounder.founder_id)).all()
    by_name_company: dict[tuple[str, uuid.UUID], Founder] = {}
    fmap = {f.id: f for f in founders}
    for cid, fid in links:
        f = fmap.get(fid)
        if f and f.normalized_name:
            by_name_company[(f.normalized_name, cid)] = f

    for group in groups.values():
        keys = {m.hard_key for m in group if m.hard_key}
        founder = next((by_url[k] for k in keys if k in by_url), None) or next(
            (by_name_company[(m.norm, m.company_id)] for m in group if (m.norm, m.company_id) in by_name_company), None)
        if founder is None:
            founder = Founder(id=uuid.uuid4(), full_name=group[0].raw, other_urls=[])
            session.add(founder)
            report.founders_created += 1
        founder.full_name = Counter(m.raw for m in group).most_common(1)[0][0][:300]
        founder.normalized_name = Counter(m.norm for m in group).most_common(1)[0][0][:300]
        linkedin = sorted(k for k in keys if k.startswith("linkedin.com/"))
        if linkedin and not founder.linkedin_url:
            founder.linkedin_url = f"https://www.{linkedin[0]}"
        others = set(founder.other_urls or []) | {f"https://{k}" for k in keys if not k.startswith("linkedin.com/")}
        founder.other_urls = sorted(others)
        session.flush()
        for k in keys:
            by_url[k] = founder
        for company_id, ms in _by_company(group).items():
            row = session.scalar(select(CompanyFounder).where(CompanyFounder.company_id == company_id,
                                                              CompanyFounder.founder_id == founder.id))
            if row is None:
                session.add(CompanyFounder(id=uuid.uuid4(), company_id=company_id, founder_id=founder.id,
                                           role=_best_role(ms), is_current=True))
            by_name_company[(founder.normalized_name, company_id)] = founder
        session.execute(update(Discovery).where(Discovery.id.in_([m.discovery.id for m in group]))
                        .values(founder_id=founder.id, resolved_at=now))
    session.flush()
    report.founders = len(groups)


def _by_company(group: list[_Mention]) -> dict[uuid.UUID, list[_Mention]]:
    out: dict[uuid.UUID, list[_Mention]] = defaultdict(list)
    for m in group:
        out[m.company_id].append(m)
    return out


def _best_role(ms: list[_Mention]) -> str:
    roles = [m.role for m in ms if m.role and m.role != "founder"]
    return (roles[0] if roles else "founder")[:200]


def store_pairs(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    insert = _insert(session)
    unique = {r["pair_key"]: r for r in rows}  # last decision wins within a run
    now = utcnow()
    stmt = insert(ResolutionPair.__table__).values([{**r, "id": uuid.uuid4(), "decided_at": now} for r in unique.values()])
    session.execute(stmt.on_conflict_do_nothing(index_elements=["pair_key"]))


def remove_orphans(session: Session) -> int:
    """Companies that nothing references any more (no discovery, claim, founder link, round or candidate),
    e.g. after clusters change between runs."""
    referenced = select(Discovery.company_id).where(Discovery.company_id.is_not(None))
    orphans = session.scalars(select(Company.id).where(
        Company.id.not_in(referenced),
        Company.id.not_in(select(Candidate.company_id)),
        Company.id.not_in(select(Round.company_id)),
        Company.id.not_in(select(CompanyFounder.company_id)),
        Company.id.not_in(select(Claim.entity_id).where(Claim.entity_type == "company")),
    )).all()
    if orphans:
        session.execute(delete(CompanyAlias).where(CompanyAlias.company_id.in_(orphans)))
        session.execute(delete(Company).where(Company.id.in_(orphans)))
    return len(orphans)


# --- entry point -------------------------------------------------------------------------------------


async def resolve(
    session: Session, cfg: AppConfig, *, gemini: Any = None, embedder: Any = None, router: Any = None,
    fetcher: Any = None, find_websites: bool = True,
) -> ResolveReport:
    conf = cfg.resolve
    report = ResolveReport()
    discoveries = session.scalars(select(Discovery).order_by(Discovery.created_at)).all()
    report.discoveries = len(discoveries)
    records = build_records(discoveries, conf)
    report.records = len(records)
    stored = {p.pair_key: (p.decision, p.method, p.reason) for p in session.scalars(select(ResolutionPair)).all()}

    uf, pair_rows = await cluster(records, conf, gemini=gemini, embedder=embedder, stored=stored, report=report)
    clusters = make_clusters(records, uf, conf)

    # Reuse domains already known for linked companies, so websites aren't searched again on re-runs.
    disc_company = {d.id: d.company_id for d in discoveries}
    known = {c.id: c for c in session.scalars(select(Company).where(Company.domain.is_not(None))).all()}
    for cl in clusters:
        if not cl.domain:
            for cid in {disc_company.get(i) for i in cl.discovery_ids} - {None}:
                if cid in known:
                    cl.domain, cl.website = known[cid].domain, known[cid].website
                    break

    if find_websites and conf.get("website_lookup", {}).get("enabled", True) and router is not None and fetcher is not None:
        await lookup_websites(clusters, conf, router=router, fetcher=fetcher, report=report)
    clusters = merge_by_domain(clusters, conf)
    _count_methods(clusters, pair_rows)

    store_pairs(session, pair_rows)
    persist_companies(session, clusters, conf, report)
    persist_founders(session, report)
    from screener.resolve.founders import merge_duplicate_founders

    if merged := merge_duplicate_founders(session):
        report.notes.append(f"merged {len(merged)} duplicate founder records (same person, name order differs)")
    removed = remove_orphans(session)
    if removed:
        report.notes.append(f"removed {removed} orphaned companies")
    from screener.reviewer import maybe_apply

    if (picks := maybe_apply(session)) is not None:  # reviewer notes follow their company through merges
        report.notes.append(f"reviewer picks: {len(picks.linked)} linked, {picks.written} notes written"
                            + (f", unresolved {', '.join(picks.unresolved)}" if picks.unresolved else ""))
    report.clusters = sorted(clusters, key=lambda c: (len(c.discovery_ids), len(c.records)), reverse=True)
    return report


def _count_methods(clusters: list[Cluster], pair_rows: list[dict[str, Any]]) -> None:
    owner: dict[str, Cluster] = {r.key: cl for cl in clusters for r in cl.records}
    by_pair: dict[str, tuple[str, str]] = {}
    for cl in clusters:
        for a in cl.records:
            for b in cl.records:
                if a.key < b.key:
                    by_pair[pair_key(a, b)] = (a.key, b.key)
    for row in pair_rows:
        if row["decision"] == "merge" and row["pair_key"] in by_pair:
            a_key, _ = by_pair[row["pair_key"]]
            owner[a_key].methods[row["method"]] += 1
