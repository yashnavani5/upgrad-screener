"""Entity resolution: normalization, pair rules on tricky pairs, union-find guard, idempotent persistence."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from screener.config import load_yaml
from screener.models import Base, Company, CompanyAlias, CompanyFounder, Discovery, Founder, ResolutionPair
from screener.resolve.matching import (
    DomainUnionFind, Record, candidate_pairs, canonical_name, decide, features, jaro_winkler,
)
from screener.resolve.normalize import (
    company_domain, domain_label, looks_like_person, normalize_name, normalize_person, person_hard_key,
    registered_domain,
)
from screener.resolve.service import build_records, resolve
from screener.resolve.website import verify_homepage

CONF = load_yaml("resolve")
SUFFIXES = CONF["trailing_suffixes"]
SHARED = CONF["shared_domains"]
T = CONF["thresholds"]


# --- normalization ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("XYZ Learning Pvt Ltd", "xyz"),
        ("xyz.in", "xyz"),
        ("www.xyz.co.in", "xyz"),
        ("Acme Technologies Private Limited", "acme"),
        ("Acme Labs, Inc.", "acme"),
        ("Brightpath Academy LLP", "brightpath"),
        ("Learning", "learning"),             # never stripped to empty
        ("Learning Labs", "learning"),
        ("Byju's", "byjus"),
        ("Ēdu Café", "edu cafe"),              # transliterated
        ("Edge", "edge"),
        ("EdTech Academy", "edtech"),
        ("Learning Curve Ventures", "learning curve ventures"),  # suffixes only stripped when trailing
    ],
)
def test_normalize_name(raw: str, norm: str) -> None:
    assert normalize_name(raw, SUFFIXES) == norm


@pytest.mark.parametrize(
    "url,domain",
    [
        ("https://www.xyz.co.in/about", "xyz.co.in"),
        ("xyz.in", "xyz.in"),
        ("https://app.acme.ai/login?x=1", "acme.ai"),
        ("https://someone.github.io/project", "someone.github.io"),  # github.io is a public suffix
        ("not a url", None),
        (None, None),
    ],
)
def test_registered_domain(url, domain) -> None:
    assert registered_domain(url) == domain


def test_shared_platform_urls_are_not_company_domains() -> None:
    assert company_domain("https://github.com/acme-org", SHARED) is None
    assert company_domain("https://www.linkedin.com/company/acme", SHARED) is None
    assert company_domain("https://www.ycombinator.com/companies/acme", SHARED) is None
    assert company_domain("https://acme.in/", SHARED) == "acme.in"


def test_people() -> None:
    assert normalize_person("Dr. Praveen  Parameswar") == "praveen parameswar"
    assert looks_like_person("Asha Rao") and not looks_like_person("hneemann") and not looks_like_person("Acme 2")
    assert person_hard_key("https://in.linkedin.com/in/AshaRao/?trk=x") == "linkedin.com/in/asharao"
    assert person_hard_key("https://github.com/hneemann/Digital") == "github.com/hneemann"
    assert person_hard_key("https://twitter.com/asha") == "x.com/asha"
    assert person_hard_key("https://acme.in/team") is None


# --- tricky pairs ----------------------------------------------------------------------------------


def rec(name: str, url: str | None = None, founders: tuple[str, ...] = (), description: str = "", key: str | None = None) -> Record:
    domain = company_domain(url, SHARED) if url else None
    if domain is None and registered_domain(name) and "." in name:
        domain = registered_domain(name)
    return Record(key=key or f"k-{name}-{url}-{description[:10]}", name=name, norm=normalize_name(name, SUFFIXES),
                  domain=domain, founders={normalize_person(f) for f in founders}, description=description,
                  raw_names=[name], discovery_ids=[uuid.uuid4()], scouts={"test"})


def test_xyz_learning_vs_xyz_in_merge_on_domain() -> None:
    a = rec("XYZ Learning Pvt Ltd", url="https://www.xyz.in/about")
    b = rec("xyz.in")
    assert decide(features(a, b), T) == ("merge", "domain")


def test_xyz_learning_without_url_vs_xyz_in_goes_to_tiebreak() -> None:
    a, b = rec("XYZ Learning Pvt Ltd"), rec("xyz.in")
    f = features(a, b)
    assert f.jw == 1.0
    assert decide(f, T) == ("ambiguous", "high_jw_unsupported")


def test_two_different_edge_companies_never_merge_on_name() -> None:
    a = rec("Edge", url="https://edge.ai", description="Edge AI chips for cameras")
    b = rec("Edge Learning", url="https://edgeedu.in", description="Tutoring for Class 10 students")
    f = features(a, b)
    assert f.jw == 1.0  # "edge" == "edge" after suffix stripping
    assert decide(f, T) == ("separate", "domain_conflict")


def test_same_name_with_shared_founder_merges() -> None:
    a = rec("Brightly", founders=("Asha Rao",))
    b = rec("Brightly Learning", founders=("Asha Rao", "Vikram Singh"))
    assert decide(features(a, b), T) == ("merge", "rule:name+founder")


def test_same_name_with_similar_description_merges() -> None:
    a, b = rec("Brightly"), rec("Brightly")
    f = features(a, b, [1.0, 0.0, 0.1], [0.98, 0.02, 0.12])
    assert decide(f, T) == ("merge", "rule:name+description")


def test_jw_band_is_ambiguous_and_low_similarity_separate() -> None:
    a, b = rec("Brightly"), rec("Brighton")  # JW 0.90
    assert T["jw_ambiguous"] <= jaro_winkler(a.norm, b.norm) < T["jw_merge"]
    assert decide(features(a, b), T) == ("ambiguous", "jw_band")
    assert decide(features(rec("Brightly"), rec("Quadrant")), T) == ("separate", "low_similarity")


def test_blocking_only_pairs_sharing_a_key() -> None:
    records = [rec("Acme Learning"), rec("Acme Labs"), rec("Zeta", founders=("Asha Rao",)), rec("Omega", founders=("Meera Rao",))]
    for r in records:
        r.surnames = {n.split()[-1] for n in r.founders}
    pairs = candidate_pairs(records, 4)
    assert (0, 1) in pairs          # same name prefix "acme"
    assert (2, 3) in pairs          # same founder surname "rao"
    assert (0, 2) not in pairs


def test_union_find_refuses_to_join_different_domains() -> None:
    records = [rec("A", url="https://x.com.au"), rec("B"), rec("C", url="https://y.in")]
    uf = DomainUnionFind(records)
    assert uf.union(0, 1)
    assert not uf.union(1, 2)  # B is now in x.com.au's component; y.in can't join
    assert len(uf.groups()) == 2


def test_canonical_name_prefers_frequency_and_domain_match() -> None:
    rs = [rec("XYZ Learning Pvt Ltd"), rec("xyz.in"), rec("XYZ"), rec("XYZ")]
    for r in rs:
        r.domain = "xyz.in"
    assert canonical_name(rs, "xyz.in", domain_label) == "XYZ"


def test_website_verification_requires_mention_and_domain_for_short_names() -> None:
    assert verify_homepage(["Acme Learning"], "acmelearning.in", "Acme Learning | AI tutoring", "", SUFFIXES)
    assert verify_homepage(["Acme Learning"], "acmelearning.in", "Welcome", "Something else entirely", SUFFIXES) is None
    assert verify_homepage(["Quad"], "randomblog.com", "Quad is great", "", SUFFIXES) is None  # short name, wrong domain
    assert verify_homepage(["Quad"], "quad.so", "Quad | Peer learning", "", SUFFIXES)


# --- end to end on SQLite ---------------------------------------------------------------------------


class FakeGemini:
    """Tiebreak double: same company only if both descriptions mention the same subject word."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, prompt, schema, model="fast", *, system=None, use_cache=True):
        self.calls += 1
        a, b = prompt.split("RECORD B:")
        same = ("tutor" in a and "tutor" in b) or ("chip" in a and "chip" in b)
        return schema(same=same, reason="fake tiebreak")


class FakeEmbedder:
    async def embed(self, texts, task="SEMANTIC_SIMILARITY"):
        return SimpleNamespace(model="fake", vectors=[[1.0 if "tutor" in t else 0.0, 1.0 if "chip" in t else 0.0, 0.01] for t in texts])


def add(session: Session, scout: str, name: str, *, url: str | None = None, person: str | None = None,
        person_url: str | None = None, source: str | None = None, snippet: str = "") -> None:
    session.add(Discovery(
        id=uuid.uuid4(), batch_id=uuid.uuid4(), scout=scout, raw_company_name=name, raw_person_name=person,
        company_url=url, person_url=person_url, source_url=source or f"https://src.example/{scout}/{name}",
        snippet=snippet, extra={}, dedupe_key=uuid.uuid4().hex,
    ))


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def run(session: Session, gemini=None) -> object:
    cfg = SimpleNamespace(resolve=CONF)
    report = asyncio.run(resolve(session, cfg, gemini=gemini, embedder=FakeEmbedder(), find_websites=False))
    session.commit()
    return report


def counts(session: Session) -> tuple[int, int, int, int]:
    return tuple(session.scalar(select(func.count()).select_from(t)) for t in (Company, Founder, CompanyAlias, CompanyFounder))


def seed(session: Session) -> None:
    add(session, "lists", "XYZ Learning Pvt Ltd", url="https://www.xyz.in/about", snippet="AI tutor for school students")
    add(session, "betalist", "xyz.in", snippet="AI tutor app")
    add(session, "accelerators", "Edge", url="https://edge.ai", snippet="Edge AI chips")
    add(session, "vc_portfolios", "Edge Learning", url="https://edgeedu.in", snippet="Online tutor marketplace",
        person="Asha Rao", source="https://vc.example/portfolio")
    add(session, "vc_portfolios", "Edge Learning", url="https://edgeedu.in", snippet="Online tutor marketplace",
        person="Vikram Singh", source="https://vc.example/portfolio")
    add(session, "xray", "Edge Learning", person="Asha Rao", person_url="https://in.linkedin.com/in/asharao")
    add(session, "github", "hneemann", url="https://github.com/hneemann", person="hneemann",
        person_url="https://github.com/hneemann", source="https://github.com/hneemann/Digital", snippet="logic simulator")
    session.commit()


def test_resolve_end_to_end_and_idempotent(session: Session) -> None:
    seed(session)
    gemini = FakeGemini()
    first = run(session, gemini)
    companies = {c.name: c for c in session.scalars(select(Company)).all()}
    assert set(companies) == {"XYZ Learning Pvt Ltd", "Edge", "Edge Learning", "hneemann"} or \
        set(companies) == {"xyz.in", "Edge", "Edge Learning", "hneemann"} or len(companies) == 4
    assert companies["Edge"].domain == "edge.ai" and companies["Edge Learning"].domain == "edgeedu.in"
    xyz = next(c for c in companies.values() if c.domain == "xyz.in")
    aliases = set(session.scalars(select(CompanyAlias.alias).where(CompanyAlias.company_id == xyz.id)))
    assert aliases == {"XYZ Learning Pvt Ltd", "xyz.in"}
    # Every discovery links to its canonical company.
    assert session.scalar(select(func.count()).select_from(Discovery).where(Discovery.company_id.is_(None))) == 0
    # Founders: Asha Rao (portfolio + LinkedIn) is one person; GitHub handle is not a founder.
    founders = {f.full_name: f for f in session.scalars(select(Founder)).all()}
    assert set(founders) == {"Asha Rao", "Vikram Singh"}
    assert founders["Asha Rao"].linkedin_url == "https://www.linkedin.com/in/asharao"
    before = counts(session)
    pairs_before = session.scalar(select(func.count()).select_from(ResolutionPair))

    second = run(session, gemini)  # idempotent: nothing new
    assert counts(session) == before
    assert session.scalar(select(func.count()).select_from(ResolutionPair)) == pairs_before
    assert second.companies_created == 0 and second.founders_created == 0
    assert first.companies_created == 4


def test_new_discovery_with_domain_merges_existing_companies(session: Session) -> None:
    add(session, "hn", "Lingo Loop", snippet="Spanish tutor you can talk to")
    add(session, "betalist", "LingoLoop App", url="https://lingoloop.io", snippet="language exchange community")
    session.commit()
    run(session, FakeGemini())
    n_before = session.scalar(select(func.count()).select_from(Company))
    assert n_before == 2  # no bridge yet: one company has no domain
    add(session, "lists", "Lingo Loop", url="https://www.lingoloop.io", snippet="AI tutor for languages")
    session.commit()
    run(session, FakeGemini())
    loop = session.scalars(select(Company).where(Company.domain == "lingoloop.io")).all()
    assert len(loop) == 1
    linked = session.scalars(select(Discovery.company_id).where(Discovery.raw_company_name.like("Lingo%"))).all()
    assert len(set(linked)) == 1
    assert session.scalar(select(func.count()).select_from(Company)) == 1  # the two existing companies merged


def test_build_records_groups_same_source_rows(session: Session) -> None:
    seed(session)
    records = build_records(session.scalars(select(Discovery)).all(), CONF)
    edge = [r for r in records if r.norm == "edge" and "vc_portfolios" in r.scouts]
    assert len(edge) == 1 and edge[0].founders == {"asha rao", "vikram singh"} and len(edge[0].discovery_ids) == 2
    gh = next(r for r in records if r.norm == "hneemann")
    assert gh.domain is None and gh.founders == set()  # github.com is shared; a handle is not a person


# --- duplicate founders within a company ---------------------------------------------------------------

from screener.resolve.founders import duplicate_groups  # noqa: E402
from screener.resolve.normalize import same_person  # noqa: E402


def test_same_person_name_order_and_contained_names() -> None:
    assert same_person("Minh Thu Do", "Do Minh Thu")          # same tokens, other order
    assert same_person("Lam Do", "Do Ngoc Lam")               # 2-token name contained in the 3-token name
    assert same_person("Dr. Justin Weinberg", "Justin Weinberg")
    assert not same_person("Rahul", "Rahul Sharma")           # one token is never enough
    assert not same_person("Rahul Sharma", "Rahul Verma")
    assert same_person("Sameer S.", "Sameer Sadana")           # abbreviated surname
    assert not same_person("Sameer S.", "Sameer Verma")
    assert not same_person("Sameer S.", "Rahul Sadana")


def test_duplicate_groups_are_transitive() -> None:
    groups = duplicate_groups({1: "Do Minh Thu", 2: "Minh Thu Do", 3: "Do Ngoc Lam", 4: "Lam Do", 5: "Anna Kim"})
    assert sorted(sorted(g) for g in groups) == [[1, 2], [3, 4]]
