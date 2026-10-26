"""SearchRouter: SearXNG, ddgs, Exa, Tavily, SerpAPI and Gemini grounding behind one interface.

Strategies (config/providers.yaml): cheap (SearXNG -> ddgs first), semantic (Exa first),
quality (grounding/SerpAPI first). Falls back on error, missing key or exhausted quota.
Exa, Tavily and SerpAPI are called over their REST APIs with httpx, so every provider shares the same
async client, rate limiter, retry policy and call logging.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from screener.cache import DiskCache, get_cache
from screener.config import AppConfig, get_config
from screener.db import QuotaStore
from screener.ratelimit import RETRYABLE_STATUS, RateLimiter, RetryableError, get_limiter, parse_retry_after, retry_async
from screener.telemetry import CallRecord, arecord, timed
from screener.urls import dedupe_key, normalize_url

log = logging.getLogger(__name__)

PROVIDER_TIMEOUT_SECONDS = 45  # a hung provider falls through to the next one


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    provider: str
    rank: int


class ProviderUnavailable(RuntimeError):
    """Provider cannot serve this run (e.g. grounding probe failed). Router skips it."""


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, n: int) -> list[SearchResult]: ...


@dataclass(frozen=True)
class QuotaPolicy:
    requires_quota: bool = False
    period: str | None = None  # month | day
    limit: float | None = None
    cost_per_call: float = 1.0


@dataclass
class Attempt:
    provider: str
    outcome: str  # ok | empty | error | skipped | cache
    count: int = 0
    detail: str | None = None


@dataclass
class RouterResult:
    query: str
    strategy: str
    results: list[SearchResult]
    attempts: list[Attempt] = field(default_factory=list)


def period_key(period: str | None, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d") if period == "day" else now.strftime("%Y-%m")


def dedupe(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        key = dedupe_key(r.url)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _results(provider: str, rows: list[dict[str, Any]], n: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for row in rows:
        url = (row.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        out.append(
            SearchResult(
                title=(row.get("title") or "").strip(),
                url=normalize_url(url),
                snippet=" ".join((row.get("snippet") or "").split())[:600],
                provider=provider,
                rank=len(out) + 1,
            )
        )
        if len(out) >= n:
            break
    return out


class HttpProvider:
    """Shared plumbing: rate limit, retry on 429/5xx honoring Retry-After, call logging (no secrets)."""

    name = "http"

    def __init__(self, client: httpx.AsyncClient, limiter: RateLimiter, attempts: int = 3) -> None:
        self.client = client
        self.limiter = limiter
        self.attempts = attempts

    async def _request(self, method: str, url: str, log_endpoint: str, **kwargs: Any) -> Any:
        key = f"provider:{self.name}"

        async def attempt() -> httpx.Response:
            async with self.limiter.limit(key):
                resp = await self.client.request(method, url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS:
                retry_after = parse_retry_after(resp.headers.get("retry-after"))
                if retry_after:
                    self.limiter.pause(key, retry_after)
                raise RetryableError(f"HTTP {resp.status_code}", retry_after, resp.status_code)
            return resp

        with timed() as t:
            try:
                resp = await retry_async(attempt, attempts=self.attempts)
            except (RetryableError, httpx.HTTPError) as exc:
                await arecord(CallRecord(self.name, log_endpoint, "error", getattr(exc, "status", None), t["ms"], error=str(exc) or type(exc).__name__))
                raise
        status = "ok" if resp.is_success else f"http_{resp.status_code}"
        await arecord(CallRecord(self.name, log_endpoint, status, resp.status_code, t["ms"]))
        if not resp.is_success:
            raise RuntimeError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()


class SearxngProvider(HttpProvider):
    name = "searxng"

    def __init__(self, base_url: str, client: httpx.AsyncClient, limiter: RateLimiter) -> None:
        super().__init__(client, limiter)
        self.base_url = base_url.rstrip("/")

    async def search(
        self, query: str, n: int, categories: str | None = None, time_range: str | None = None
    ) -> list[SearchResult]:
        params: dict[str, Any] = {"q": query, "format": "json", "language": "en-IN", "pageno": 1}
        if categories:
            params["categories"] = categories
        if time_range:
            params["time_range"] = time_range
        data = await self._request("GET", f"{self.base_url}/search", f"{self.base_url}/search", params=params)
        rows = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} for r in data.get("results", [])]
        return _results(self.name, rows, n)


class DdgsProvider:
    name = "ddgs"

    def __init__(self, limiter: RateLimiter, backend: str = "auto") -> None:
        self.limiter = limiter
        self.backend = backend  # ddgs engines, comma-separated (config/providers.yaml search.providers.ddgs.backend)

    async def search(self, query: str, n: int) -> list[SearchResult]:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        def run() -> list[dict[str, Any]]:
            try:
                return DDGS().text(query, region="in-en", max_results=n, backend=self.backend)
            except DDGSException as exc:
                if "no results" in str(exc).lower():
                    return []  # an empty result, not a failure
                raise

        with timed() as t:
            try:
                async with self.limiter.limit(f"provider:{self.name}"):
                    raw = await asyncio.to_thread(run)
            except Exception as exc:
                await arecord(CallRecord(self.name, "text", "error", latency_ms=t["ms"], error=str(exc)[:300]))
                raise
        await arecord(CallRecord(self.name, "text", "ok", latency_ms=t["ms"]))
        rows = [{"title": r.get("title"), "url": r.get("href"), "snippet": r.get("body")} for r in raw]
        return _results(self.name, rows, n)


class ExaProvider(HttpProvider):
    name = "exa"

    def __init__(self, api_key: str, client: httpx.AsyncClient, limiter: RateLimiter) -> None:
        super().__init__(client, limiter)
        self.api_key = api_key

    async def search(self, query: str, n: int) -> list[SearchResult]:
        data = await self._request(
            "POST", "https://api.exa.ai/search", "https://api.exa.ai/search",
            headers={"x-api-key": self.api_key},
            json={"query": query, "numResults": n, "type": "auto", "contents": {"highlights": {"numSentences": 2}}},
        )
        rows = [
            {"title": r.get("title"), "url": r.get("url"), "snippet": " ".join(r.get("highlights") or []) or (r.get("text") or "")[:400]}
            for r in data.get("results", [])
        ]
        return _results(self.name, rows, n)


class TavilyProvider(HttpProvider):
    name = "tavily"

    def __init__(self, api_key: str, client: httpx.AsyncClient, limiter: RateLimiter) -> None:
        super().__init__(client, limiter)
        self.api_key = api_key

    async def search(self, query: str, n: int) -> list[SearchResult]:
        data = await self._request(
            "POST", "https://api.tavily.com/search", "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "max_results": n, "search_depth": "basic"},
        )
        rows = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} for r in data.get("results", [])]
        return _results(self.name, rows, n)


class SerpApiProvider(HttpProvider):
    name = "serpapi"

    def __init__(self, api_key: str, client: httpx.AsyncClient, limiter: RateLimiter) -> None:
        super().__init__(client, limiter)
        self.api_key = api_key

    async def search(self, query: str, n: int) -> list[SearchResult]:
        data = await self._request(
            "GET", "https://serpapi.com/search.json", "https://serpapi.com/search.json",
            params={"engine": "google", "q": query, "num": n, "gl": "in", "hl": "en", "api_key": self.api_key},
        )
        rows = [{"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")} for r in data.get("organic_results", [])]
        return _results(self.name, rows, n)

    async def account(self) -> dict[str, Any]:
        """Free endpoint: plan and searches left this month (does not consume quota)."""
        return await self._request(
            "GET", "https://serpapi.com/account.json", "https://serpapi.com/account.json", params={"api_key": self.api_key}
        )


class GeminiGroundingProvider:
    name = "gemini_grounding"

    def __init__(self, gemini: Any) -> None:
        self.gemini = gemini

    async def search(self, query: str, n: int) -> list[SearchResult]:
        if not await self.gemini.probe_grounding():
            raise ProviderUnavailable(f"grounding unavailable: {self.gemini.grounding_reason}")
        rows = await self.gemini.grounded_search(query, n)
        return _results(self.name, rows, n)


class SearchRouter:
    def __init__(
        self,
        providers: dict[str, SearchProvider],
        strategies: dict[str, list[str]],
        policies: dict[str, QuotaPolicy] | None = None,
        quota: QuotaStore | None = None,
        cache: DiskCache | None = None,
        disabled_reasons: dict[str, str] | None = None,
    ) -> None:
        self.providers = providers
        self.strategies = strategies
        self.policies = policies or {}
        self.quota = quota
        self.cache = cache
        self.disabled_reasons = disabled_reasons or {}

    async def search(
        self, query: str, strategy: str = "cheap", n: int = 10, min_results: int = 1
    ) -> RouterResult:
        if strategy not in self.strategies:
            raise ValueError(f"unknown strategy {strategy!r}; choose from {sorted(self.strategies)}")
        out = RouterResult(query=query, strategy=strategy, results=[])
        merged: list[SearchResult] = []
        for name in self.strategies[strategy]:
            provider = self.providers.get(name)
            if provider is None:
                out.attempts.append(Attempt(name, "skipped", detail=self.disabled_reasons.get(name, "disabled")))
                continue
            cache_key = {"provider": name, "query": query, "n": n}
            cached = self.cache.get("results", cache_key) if self.cache else None
            if cached is not None:
                found = [SearchResult.model_validate(r) for r in cached]
                out.attempts.append(Attempt(name, "cache", len(found)))
            else:
                policy = self.policies.get(name, QuotaPolicy())
                period = period_key(policy.period)
                if policy.requires_quota:
                    if self.quota is None:
                        out.attempts.append(Attempt(name, "skipped", detail="quota store unavailable (DB down)"))
                        continue
                    if not self.quota.try_consume(name, period, policy.cost_per_call, policy.limit):
                        out.attempts.append(Attempt(name, "skipped", detail=f"quota exhausted ({policy.limit}/{policy.period})"))
                        continue
                try:
                    found = await asyncio.wait_for(provider.search(query, n), timeout=PROVIDER_TIMEOUT_SECONDS)
                except ProviderUnavailable as exc:
                    if policy.requires_quota and self.quota is not None:
                        self.quota.release(name, period, policy.cost_per_call)
                    out.attempts.append(Attempt(name, "skipped", detail=str(exc)))
                    continue
                except Exception as exc:  # fail soft: log and fall through to the next provider
                    if policy.requires_quota and self.quota is not None:
                        self.quota.release(name, period, policy.cost_per_call)
                    log.warning("search provider %s failed: %s", name, exc)
                    out.attempts.append(Attempt(name, "error", detail=f"{type(exc).__name__}: {str(exc)[:200]}"))
                    continue
                if self.cache is not None and found:
                    self.cache.set("results", cache_key, [r.model_dump() for r in found])
                out.attempts.append(Attempt(name, "ok" if found else "empty", len(found)))
            merged = dedupe(merged + found)
            if len(merged) >= min_results:
                break
        out.results = [r.model_copy(update={"rank": i}) for i, r in enumerate(merged[:n], start=1)]
        return out

    @classmethod
    def from_config(
        cls,
        cfg: AppConfig | None = None,
        *,
        client: httpx.AsyncClient,
        quota: QuotaStore | None,
        gemini: Any = None,
        limiter: RateLimiter | None = None,
    ) -> SearchRouter:
        cfg = cfg or get_config()
        s = cfg.settings
        limiter = limiter or get_limiter()
        search_cfg = cfg.providers.get("search", {})
        statuses = cfg.provider_statuses()
        providers: dict[str, SearchProvider] = {}
        if s.searxng_url:
            providers["searxng"] = SearxngProvider(s.searxng_url, client, limiter)
        providers["ddgs"] = DdgsProvider(limiter, str(search_cfg.get("providers", {}).get("ddgs", {}).get("backend", "auto")))
        if s.exa_api_key:
            providers["exa"] = ExaProvider(s.exa_api_key.get_secret_value(), client, limiter)
        if s.tavily_api_key:
            providers["tavily"] = TavilyProvider(s.tavily_api_key.get_secret_value(), client, limiter)
        if s.serpapi_api_key:
            providers["serpapi"] = SerpApiProvider(s.serpapi_api_key.get_secret_value(), client, limiter)
        if gemini is not None:
            providers["gemini_grounding"] = GeminiGroundingProvider(gemini)
        policies = {
            name: QuotaPolicy(
                requires_quota=bool(spec.get("requires_quota")),
                period=spec.get("period"),
                limit=spec.get("limit"),
                cost_per_call=float(spec.get("cost_per_call", 1)),
            )
            for name, spec in search_cfg.get("providers", {}).items()
        }
        disabled = {n: st.reason for n, st in statuses.items() if not st.enabled}
        return cls(
            providers=providers,
            strategies=search_cfg.get("strategies", {}),
            policies=policies,
            quota=quota,
            cache=get_cache("search"),
            disabled_reasons=disabled,
        )
