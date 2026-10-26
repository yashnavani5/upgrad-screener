"""Find a company's official website: one "cheap" search, then verify the homepage mentions the company."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import lxml.html

from screener.http import Fetcher
from screener.resolve.matching import jaro_winkler
from screener.resolve.normalize import domain_label, is_listed_domain, normalize_name, registered_domain
from screener.scouts.grounding import name_in_text, normalize
from screener.search import SearchRouter

SHORT_NAME = 5
LABEL_MATCH = 0.85
HEAD_CHARS = 3000


@dataclass(frozen=True)
class WebsiteMatch:
    domain: str
    homepage: str
    evidence_span: str
    retrieved_at: str
    query: str


def _title_and_text(html: str) -> tuple[str, str]:
    doc = lxml.html.fromstring(html)
    title = " ".join(doc.xpath("//title//text()")).strip()
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    text = re.sub(r"\s+", " ", doc.text_content()).strip()
    return title, text


def _evidence(name: str, title: str, text: str) -> str | None:
    """Short verbatim window around the first mention of `name` (title first)."""
    for source in (title, text[:HEAD_CHARS]):
        if source and name_in_text(name, source):
            idx = normalize(source).find(normalize(name))
            if idx < 0:
                return source[:200]
            start = max(0, idx - 60)
            return source[start : start + 200].strip()
    return None


def verify_homepage(names: list[str], domain: str, title: str, text: str, suffixes: list[str]) -> str | None:
    """Evidence span if the homepage mentions the company; short names also need the domain to match."""
    label = (domain_label(domain) or "").replace("-", "")
    for name in names:
        span = _evidence(name, title, text)
        if not span:
            continue
        norm = normalize_name(name, suffixes).replace(" ", "")
        if len(norm) < SHORT_NAME and jaro_winkler(norm, label) < LABEL_MATCH:
            continue  # "Quad" mentioned on some page is not enough; quad.<tld> would be
        return span
    return None


async def find_website(
    router: SearchRouter, fetcher: Fetcher, names: list[str], *, shared: list[str], non_company: list[str],
    suffixes: list[str], results_considered: int = 5, strategy: str = "cheap", fallbacks: bool = True,
) -> WebsiteMatch | None:
    """`strategy`: router strategy ("quality" = Gemini grounding first, not throttled like free search engines).
    Whatever the search returns, the homepage must still name the company (verify_homepage)."""
    name = names[0]
    query = f'"{name}" official website'
    res = await router.search(query, strategy=strategy, n=10)
    tried: set[str] = set()
    for r in res.results:
        domain = registered_domain(r.url)
        if not domain or domain in tried or is_listed_domain(domain, shared) or is_listed_domain(domain, non_company):
            continue
        tried.add(domain)
        if len(tried) > results_considered:
            break
        parts = urlsplit(r.url)
        homepage = f"{parts.scheme}://{parts.netloc}/"
        page = await fetcher.fetch(homepage, allow_fallbacks=fallbacks)
        if not page.ok or not page.html:
            continue
        title, text = _title_and_text(page.html)
        span = verify_homepage(names, domain, title, text, suffixes)
        if span:
            return WebsiteMatch(domain=domain, homepage=page.final_url or homepage, evidence_span=span,
                                retrieved_at=page.fetched_at.isoformat(), query=query)
    return None
