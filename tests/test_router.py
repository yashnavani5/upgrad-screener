"""Router fallback with fake providers (test doubles live here, never in pipeline code)."""

from __future__ import annotations

from pathlib import Path

import pytest

from screener.cache import DiskCache
from screener.db import MemoryQuotaStore
from screener.search import ProviderUnavailable, QuotaPolicy, SearchResult, SearchRouter, period_key

STRATEGIES = {
    "cheap": ["searxng", "ddgs", "exa", "tavily", "serpapi", "gemini_grounding"],
    "semantic": ["exa", "searxng", "ddgs", "tavily", "serpapi", "gemini_grounding"],
    "quality": ["gemini_grounding", "serpapi", "tavily", "exa", "searxng", "ddgs"],
}


class FakeProvider:
    def __init__(self, name: str, urls: list[str] | None = None, error: Exception | None = None) -> None:
        self.name = name
        self.urls = urls or []
        self.error = error
        self.calls = 0

    async def search(self, query: str, n: int) -> list[SearchResult]:
        self.calls += 1
        if self.error:
            raise self.error
        return [SearchResult(title=u, url=u, provider=self.name, rank=i) for i, u in enumerate(self.urls[:n], 1)]


def router(providers: dict[str, FakeProvider], **kwargs) -> SearchRouter:
    return SearchRouter(providers=providers, strategies=STRATEGIES, **kwargs)  # type: ignore[arg-type]


async def test_first_provider_with_results_wins() -> None:
    sx = FakeProvider("searxng", ["https://a.com/1", "https://a.com/2"])
    dd = FakeProvider("ddgs", ["https://b.com/1"])
    res = await router({"searxng": sx, "ddgs": dd}).search("q", "cheap")
    assert [r.provider for r in res.results] == ["searxng", "searxng"]
    assert dd.calls == 0
    assert res.attempts[0].outcome == "ok"


async def test_falls_back_on_error() -> None:
    sx = FakeProvider("searxng", error=RuntimeError("connection refused"))
    dd = FakeProvider("ddgs", ["https://b.com/1"])
    res = await router({"searxng": sx, "ddgs": dd}).search("q", "cheap")
    assert [r.provider for r in res.results] == ["ddgs"]
    assert [(a.provider, a.outcome) for a in res.attempts] == [("searxng", "error"), ("ddgs", "ok")]


async def test_falls_back_on_empty() -> None:
    sx = FakeProvider("searxng", [])
    dd = FakeProvider("ddgs", ["https://b.com/1"])
    res = await router({"searxng": sx, "ddgs": dd}).search("q", "cheap")
    assert res.results[0].provider == "ddgs"
    assert res.attempts[0].outcome == "empty"


async def test_missing_provider_is_skipped_with_reason() -> None:
    dd = FakeProvider("ddgs", ["https://b.com/1"])
    res = await router({"ddgs": dd}, disabled_reasons={"searxng": "SEARXNG_URL not set"}).search("q", "cheap")
    assert res.attempts[0].provider == "searxng"
    assert res.attempts[0].outcome == "skipped"
    assert res.attempts[0].detail == "SEARXNG_URL not set"


@pytest.mark.parametrize(
    ("strategy", "first"),
    [("cheap", "searxng"), ("semantic", "exa"), ("quality", "gemini_grounding")],
)
async def test_strategy_order(strategy: str, first: str) -> None:
    providers = {n: FakeProvider(n, [f"https://{n}.com/"]) for n in STRATEGIES["cheap"]}
    quota = MemoryQuotaStore()
    res = await router(providers, quota=quota).search("q", strategy)
    assert res.results[0].provider == first


async def test_quota_exhausted_skips_provider_and_falls_back() -> None:
    exa = FakeProvider("exa", ["https://exa.com/"])
    sx = FakeProvider("searxng", ["https://sx.com/"])
    quota = MemoryQuotaStore()
    policies = {"exa": QuotaPolicy(requires_quota=True, period="month", limit=2)}
    r = router({"exa": exa, "searxng": sx}, quota=quota, policies=policies)
    for _ in range(2):
        assert (await r.search(f"q{_}", "semantic")).results[0].provider == "exa"
    res = await r.search("q3", "semantic")
    assert res.results[0].provider == "searxng"
    assert res.attempts[0].outcome == "skipped" and "quota exhausted" in (res.attempts[0].detail or "")
    assert exa.calls == 2
    assert quota.used("exa", period_key("month")) == 2


async def test_quota_refunded_on_error() -> None:
    exa = FakeProvider("exa", error=RuntimeError("500"))
    sx = FakeProvider("searxng", ["https://sx.com/"])
    quota = MemoryQuotaStore()
    policies = {"exa": QuotaPolicy(requires_quota=True, period="month", limit=5)}
    await router({"exa": exa, "searxng": sx}, quota=quota, policies=policies).search("q", "semantic")
    assert quota.used("exa", period_key("month")) == 0


async def test_paid_provider_disabled_without_quota_store() -> None:
    exa = FakeProvider("exa", ["https://exa.com/"])
    sx = FakeProvider("searxng", ["https://sx.com/"])
    policies = {"exa": QuotaPolicy(requires_quota=True, period="month", limit=100)}
    res = await router({"exa": exa, "searxng": sx}, quota=None, policies=policies).search("q", "semantic")
    assert exa.calls == 0
    assert res.results[0].provider == "searxng"
    assert "quota store unavailable" in (res.attempts[0].detail or "")


async def test_provider_unavailable_is_skipped() -> None:
    gg = FakeProvider("gemini_grounding", error=ProviderUnavailable("probe failed"))
    sp = FakeProvider("serpapi", ["https://serp.com/"])
    res = await router({"gemini_grounding": gg, "serpapi": sp}).search("q", "quality")
    assert res.attempts[0].outcome == "skipped"
    assert res.results[0].provider == "serpapi"


async def test_min_results_merges_and_dedupes_across_providers() -> None:
    sx = FakeProvider("searxng", ["https://a.com/x", "https://b.com/"])
    dd = FakeProvider("ddgs", ["https://www.a.com/x/", "https://c.com/"])
    res = await router({"searxng": sx, "ddgs": dd}).search("q", "cheap", n=10, min_results=3)
    assert [r.url for r in res.results] == ["https://a.com/x", "https://b.com/", "https://c.com/"]
    assert [r.rank for r in res.results] == [1, 2, 3]


async def test_all_fail_returns_empty_with_attempts() -> None:
    providers = {n: FakeProvider(n, error=RuntimeError("down")) for n in ("searxng", "ddgs")}
    res = await router(providers).search("q", "cheap")
    assert res.results == []
    assert {a.outcome for a in res.attempts} == {"error", "skipped"}


async def test_cache_hit_skips_provider(tmp_path: Path) -> None:
    sx = FakeProvider("searxng", ["https://a.com/"])
    r = router({"searxng": sx}, cache=DiskCache(tmp_path, "search", ttl_seconds=None))
    await r.search("q", "cheap")
    res = await r.search("q", "cheap")
    assert sx.calls == 1
    assert res.attempts[0].outcome == "cache"


async def test_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        await router({}).search("q", "fastest")
