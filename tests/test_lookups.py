"""Targeted lookups: search results are used through RouterResult.results and only pages naming the target are kept.
The router and page fetcher are in-test stand-ins (no network); no pipeline code is mocked."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import screener.enrich.lookups as lookups_mod
from screener.enrich.lookups import LookupReport, Lookups
from screener.enrich.pages import Page
from screener.search import RouterResult, SearchResult


class StubRouter:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results, self.queries = results, []

    async def search(self, query: str, strategy: str = "cheap", n: int = 10) -> RouterResult:
        self.queries.append(query)
        return RouterResult(query, strategy, self.results)


def test_pages_iterates_router_results_and_requires_the_target_name(monkeypatch) -> None:
    results = [
        SearchResult(title="Acme Learning raises funds", url="https://news.example/acme", snippet="", provider="t", rank=1),
        SearchResult(title="Unrelated story", url="https://news.example/other", snippet="", provider="t", rank=2),
        SearchResult(title="Acme Learning team", url="https://news.example/acme-2", snippet="", provider="t", rank=3),
    ]
    texts = {"https://news.example/acme": "Acme Learning is headquartered in Pune. " * 5,
             "https://news.example/acme-2": "A page that never names the company. " * 5}

    async def fake_fetch(fetcher, url, mode, fresh=False):
        return Page(url, texts[url], mode, datetime.now(timezone.utc)) if url in texts else None

    monkeypatch.setattr(lookups_mod, "fetch_page", fake_fetch)
    enricher = SimpleNamespace(conf={"lookups": {"queries": {}, "pages_per_query": 2}}, router=StubRouter(results),
                               fetcher=None, _usable=lambda url, t: True)
    lk = Lookups(enricher)
    target = SimpleNamespace(name="Acme Learning")
    pages = asyncio.run(lk._pages(target, '"Acme Learning" headquartered', ["Acme Learning"], LookupReport()))
    assert [p.url for p in pages] == ["https://news.example/acme"]  # the 2nd page never names the target
