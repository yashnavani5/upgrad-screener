"""Scout parsing/filtering on saved fixtures and fake scouts. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from screener.config import load_yaml
from screener.scouts.accelerators import filter_yc, is_education_company, yc_item
from screener.scouts.base import BaseScout, DiscoveryItem, run_scouts
from screener.scouts.betalist import parse_betalist
from screener.scouts.extract import chunks, page_text_and_links
from screener.scouts.feeds import feed_links
from screener.scouts.hn import parse_hn_title
from screener.scouts.xray import parse_linkedin_result

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def yc_companies() -> list[dict]:
    return json.loads((FIXTURES / "yc_companies_sample.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def yc_conf() -> dict:
    return load_yaml("sources")["accelerators"]["yc"]


# --- YC filtering (real yc-oss subset) -------------------------------------------------------------


def test_yc_filter_keeps_education_industry_and_tags(yc_companies, yc_conf) -> None:
    picked = {c["name"] for c in filter_yc(yc_companies, yc_conf["industries"], yc_conf["tag_keywords"])}
    for c in yc_companies:
        expected = c.get("industry") == "Education" or any(
            t.lower() in {"education", "elearning", "ai-enhanced learning"} for t in c.get("tags") or []
        )
        assert (c["name"] in picked) is expected, c["name"]


def test_yc_filter_rejects_machine_learning_companies(yc_companies, yc_conf) -> None:
    ml = [c for c in yc_companies if any("Learning" in t for t in c.get("tags") or [])
          and c.get("industry") != "Education"
          and not any(t.lower() in {"elearning", "ai-enhanced learning", "education"} for t in c["tags"])]
    assert ml, "fixture should contain Machine/Deep Learning negatives"
    for c in ml:
        assert not is_education_company(c, yc_conf["industries"], yc_conf["tag_keywords"])


def test_yc_filter_puts_india_first(yc_companies, yc_conf) -> None:
    picked = filter_yc(yc_companies, yc_conf["industries"], yc_conf["tag_keywords"])
    flags = ["india" in " ".join((c.get("regions") or []) + [c.get("all_locations") or ""]).lower() for c in picked]
    assert any(flags)
    assert flags == sorted(flags, reverse=True)  # all India-based companies come first


def test_yc_item_shape(yc_companies) -> None:
    item = yc_item(yc_companies[0], "accelerators")
    assert item.raw_company_name == yc_companies[0]["name"]
    assert item.source_url.startswith("https://www.ycombinator.com/companies/")
    assert "batch" in item.extra


# --- title / snippet parsers -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,name,yc",
    [
        ("Launch HN: Acme (YC W24) – AI tutor for high-school math", "Acme", "YC W24"),
        ("Launch HN: Brightly (YC S23) - Assessments for bootcamps", "Brightly", "YC S23"),
        ("Show HN: StudyBuddy – flashcards from your lecture notes", "StudyBuddy", None),
        ("Show HN: Lingo Loop: practice Spanish with an AI partner", "Lingo Loop", None),
        ("Launch HN: UPchieve (YC W21 Nonprofit) – Live tutoring for low-income students", "UPchieve", "YC W21"),
    ],
)
def test_parse_hn_title(title: str, name: str, yc: str | None) -> None:
    parsed = parse_hn_title(title)
    assert parsed is not None and parsed["name"] == name and parsed["yc"] == yc


@pytest.mark.parametrize(
    "title",
    ["Show HN: I built a tutor that explains calculus", "Show HN: An open-source LMS for schools",
     "Ask HN: How do you learn math?", "Show HN: A really long product name that is a sentence"],
)
def test_parse_hn_title_rejects_non_names(title: str) -> None:
    assert parse_hn_title(title) is None


@pytest.mark.parametrize(
    "title,snippet,expected",
    [
        ("Asha Rao - Co-founder - Acme Learning | LinkedIn", "", ("Asha Rao", "Co-founder", "Acme Learning")),
        ("Vikram Singh – Founder & CEO at Brightly | LinkedIn", "", ("Vikram Singh", "Founder & CEO", "Brightly")),
        ("Priya Nair - Founder | LinkedIn", "Experience: Lingo Loop · Education: IIT Madras · Location: Bengaluru",
         ("Priya Nair", "Founder", "Lingo Loop")),
        ("Rahul Mehta - Tutorly | LinkedIn", "", ("Rahul Mehta", None, "Tutorly")),
    ],
)
def test_parse_linkedin_result(title: str, snippet: str, expected: tuple) -> None:
    parsed = parse_linkedin_result(title, snippet)
    assert parsed is not None
    assert (parsed["name"], parsed["headline"], parsed["company"]) == expected


def test_parse_linkedin_result_rejects_non_person_titles() -> None:
    assert parse_linkedin_result("Top 10 edtech founders in India | LinkedIn") is None
    assert parse_linkedin_result("") is None


def test_parse_betalist_skips_boosted_rows() -> None:
    html = """
    <div class="x startup-row"><a href="/startups/french-ready"></a><div>French Ready</div><div>Build French speaking skills</div></div>
    <div class="x startup-row"><a href="/startups/promo"></a><div>Promo</div><div>Ad copy</div><div>BOOSTED</div></div>
    """
    rows = parse_betalist(html)
    assert rows == [{"name": "French Ready", "tagline": "Build French speaking skills", "path": "/startups/french-ready"}]


def test_feed_links_prefers_link_alternate() -> None:
    html = """<html><head><link rel="alternate" type="application/rss+xml" href="/feed/"></head>
    <body><a href="/rss.xml">RSS</a><a href="/about">About</a></body></html>"""
    assert feed_links(html, "https://news.example.com/") == ["https://news.example.com/feed/", "https://news.example.com/rss.xml"]


def test_page_text_keeps_cards_on_separate_lines_and_drops_linkedin_links() -> None:
    html = """<html><body><div class="grid"><div><a href="https://acme.example">Acme Learning</a></div>
    <div><a href="/co/brightly">Brightly</a></div><a href="https://www.linkedin.com/company/x">LinkedIn</a>
    <script>var x = 1;</script></div></body></html>"""
    text, links = page_text_and_links(html, "https://vc.example/portfolio")
    assert "Acme Learning\n" in text and "Brightly" in text and "var x" not in text
    assert ("Acme Learning", "https://acme.example/") in links
    assert ("Brightly", "https://vc.example/co/brightly") in links
    assert not any("linkedin.com" in u for _, u in links)


def test_chunks_overlap() -> None:
    text = "x" * 25_000
    parts = chunks(text, size=12_000, overlap=400)
    assert len(parts) == 3 and all(len(p) <= 12_000 for p in parts)


# --- runner: concurrency, limit, dedupe, fail-soft -------------------------------------------------


class _Ctx:
    sources: dict = {}
    gemini = None


class FakeScout(BaseScout):
    config_key = "none"

    def __init__(self, name: str, n: int = 0, error: bool = False, needs_llm: bool = False) -> None:
        self.name = name  # type: ignore[misc]
        self.needs_llm = needs_llm  # type: ignore[misc]
        super().__init__(_Ctx())  # type: ignore[arg-type]
        self.n, self.error = n, error

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        if self.error:
            raise RuntimeError("source down")
        items = [DiscoveryItem(scout=self.name, raw_company_name=f"Co {i}", source_url=f"https://s.example/{i}") for i in range(self.n)]
        return items + items[:2]  # duplicates must be removed


async def test_runner_limits_dedupes_and_fails_soft() -> None:
    reports = await run_scouts([FakeScout("a", n=50), FakeScout("b", error=True), FakeScout("c", n=3, needs_llm=True)], limit=30)
    by = {r.name: r for r in reports}
    assert len(by["a"].items) == 30 and by["a"].error is None
    assert by["b"].error and "source down" in by["b"].error and by["b"].items == []
    assert not by["c"].enabled and "Gemini" in by["c"].reason


def test_dedupe_key_ignores_url_noise_and_case() -> None:
    a = DiscoveryItem(scout="s", raw_company_name="Acme", source_url="https://www.x.com/a/?utm_source=z")
    b = DiscoveryItem(scout="s", raw_company_name="acme ", source_url="http://x.com/a")
    c = DiscoveryItem(scout="s", raw_company_name="Acme", raw_person_name="Asha", source_url="https://x.com/a")
    assert a.dedupe_key() == b.dedupe_key() != c.dedupe_key()


def test_persist_counts_new_rows_and_skips_duplicates() -> None:
    import uuid

    from sqlalchemy import create_engine

    from screener import db
    from screener.models import Base
    from screener.scouts.base import persist

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db.set_engine(engine)
    items = [DiscoveryItem(scout="t", raw_company_name=f"Co{i}", source_url=f"https://x.example/{i}") for i in range(3)]
    assert persist(items, uuid.uuid4()) == 3
    assert persist(items, uuid.uuid4()) == 0
    assert persist([], uuid.uuid4()) == 0


@pytest.mark.parametrize(
    "title,company",
    [
        # Real merged titles seen from SearXNG (several profiles joined with "..."): only the first counts.
        ("Saurabh Vyas - Cofounder at Edify.club, DeviceFlow.ai | Ex ...Maitreya Wagh - Building Bolna | Forbes 30U30", "Edify.club"),
        ("Deepanshu Singh - Vice-President Partnerships and Public ...Himanshu P. - Founder, IndiaFutureAI & CSEWhy", None),
        ("Rohit Gupta - Founder- Reeloid, India’s International ...Prachi Jain Windlass - South Delhi, Delhi", "Reeloid"),
        ("Samarth Ghag - Co-Founder & CTO @ College Circle AI ...Peeyush Ranjan - Fermi.ai; MyRico.ai", "College Circle AI"),
        ("Meera Iyer - Founder - Bengaluru, Karnataka, India | LinkedIn", None),  # a location is not a company
    ],
)
def test_parse_linkedin_merged_and_noisy_titles(title: str, company: str | None) -> None:
    parsed = parse_linkedin_result(title)
    assert parsed is not None and parsed["company"] == company


@pytest.mark.parametrize(
    "title,company",
    [
        ("Maitreya Wagh - Building Bolna | Forbes 30U30 | Bain | IIT", "Bolna"),
        ("Peeyush Ranjan - Fermi.ai; MyRico.ai; Gadgeteer; Angel", "Fermi.ai"),
    ],
)
def test_parse_linkedin_company_cleanup(title: str, company: str) -> None:
    parsed = parse_linkedin_result(title)
    assert parsed is not None and parsed["company"] == company
