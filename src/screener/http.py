"""Polite page fetching: policy check, robots.txt, httpx + trafilatura, Playwright fallback, Jina Reader last.

    async with Fetcher() as f:
        result = await f.fetch("https://example.com")
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import trafilatura
from protego import Protego
from pydantic import BaseModel

from screener.cache import DiskCache, get_cache
from screener.config import AppConfig, get_config
from screener.ratelimit import RETRYABLE_STATUS, RateLimiter, RetryableError, get_limiter, parse_retry_after, retry_async
from screener.telemetry import CallRecord, arecord, timed
from screener.urls import ascii_url, host_of, is_blocked_by_policy

log = logging.getLogger(__name__)

Outcome = Literal["ok", "blocked_by_policy", "blocked_by_robots", "not_html", "error"]
Method = Literal["httpx", "playwright", "jina", "none"]

HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
JS_SHELL_MARKERS = (
    'id="__next"></div>',
    'id="root"></div>',
    'id="app"></div>',
    "enable javascript",
    "requires javascript",
    "you need to enable javascript",
)


class PolicyBlocked(RuntimeError):
    """Raised by the httpx request hook if a redirect points at a policy-blocked host."""


class RobotsDisallowed(RuntimeError):
    """robots.txt does not allow this URL for our user agent."""


class HttpStatusError(RuntimeError):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status


class RawResponse(BaseModel):
    url: str
    final_url: str
    status: int
    content_type: str
    text: str


class RobotsDecision(BaseModel):
    allowed: bool
    robots_url: str
    reason: str


class FetchResult(BaseModel):
    url: str
    final_url: str | None = None
    status: int | None = None
    html: str | None = None
    text: str | None = None
    fetched_at: datetime
    method: Method = "none"
    outcome: Outcome
    robots: RobotsDecision | None = None
    from_cache: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def extract_text(html: str, url: str | None = None) -> str:
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True)
    return (text or "").strip()


def looks_js_rendered(html: str, text: str, min_chars: int) -> bool:
    if len(text) < min_chars:
        return True
    lowered = html.lower()
    return any(m in lowered for m in JS_SHELL_MARKERS) and len(text) < 3 * min_chars


class Fetcher:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        limiter: RateLimiter | None = None,
        fetch_cache: DiskCache | None = None,
        robots_cache: DiskCache | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.cfg = cfg or get_config()
        p = self.cfg.providers.get("fetch", {})
        self.timeout = float(p.get("timeout_seconds", 20))
        self.max_bytes = int(p.get("max_bytes", 5_000_000))
        self.min_text_chars = int(p.get("min_text_chars", 400))
        self.playwright_timeout_ms = int(float(p.get("playwright_timeout_seconds", 30)) * 1000)
        retry = self.cfg.providers.get("http", {})
        self.max_retries = int(retry.get("max_retries", 3))
        self.backoff_base = float(retry.get("backoff_base_seconds", 1.0))
        self.backoff_cap = float(retry.get("backoff_cap_seconds", 60.0))
        self.limiter = limiter or get_limiter()
        self.fetch_cache = fetch_cache or get_cache("fetch")
        self.robots_cache = robots_cache or get_cache("robots")
        self.user_agent = self.cfg.settings.user_agent
        self._own_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.9"},
            timeout=httpx.Timeout(self.timeout, connect=20.0),
            follow_redirects=True,
            event_hooks={"request": [self._block_policy_hosts]},
        )
        self._pw: Any = None
        self._browser: Any = None
        self._robots_mem: dict[str, Protego | None | bool] = {}
        self._crawl_delay_applied: set[str] = set()

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        if self._own_client:
            await self.client.aclose()

    @staticmethod
    async def _block_policy_hosts(request: httpx.Request) -> None:
        if is_blocked_by_policy(str(request.url)):
            raise PolicyBlocked(f"policy: never fetch {request.url.host}")

    # --- robots.txt --------------------------------------------------------------------------------

    async def robots_decision(self, url: str) -> RobotsDecision:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        robots_url = f"{origin}/robots.txt"
        entry = self.robots_cache.get("robots", origin)
        if entry is None:
            entry = await self._download_robots(robots_url)
            if entry.get("status") is None:  # network failure: retry once, never cache a transient error
                entry = await self._download_robots(robots_url)
            if entry.get("status") is not None:
                self.robots_cache.set("robots", origin, entry)
        status = entry.get("status")
        if status is not None and 200 <= status < 300:
            parser = Protego.parse(entry.get("body") or "")
            allowed = parser.can_fetch(url, self.cfg.settings.robots_token)
            self._apply_crawl_delay(parts.hostname or "", parser)
            return RobotsDecision(
                allowed=allowed,
                robots_url=robots_url,
                reason="allowed by robots.txt" if allowed else "disallowed by robots.txt",
            )
        if status is not None and 400 <= status < 500:
            return RobotsDecision(allowed=True, robots_url=robots_url, reason=f"robots.txt {status}: no restrictions")
        # RFC 9309: unreachable / 5xx robots.txt means assume complete disallow.
        why = f"robots.txt {status}" if status else f"robots.txt unreachable ({entry.get('error')})"
        return RobotsDecision(allowed=False, robots_url=robots_url, reason=f"{why}: treated as disallow (RFC 9309)")

    async def _download_robots(self, robots_url: str) -> dict[str, Any]:
        host = host_of(robots_url)
        with timed() as t:
            try:
                async with self.limiter.limit(f"domain:{host}"):
                    resp = await self.client.get(robots_url)
                body = resp.text[:500_000] if resp.status_code < 300 else ""
                result = {"status": resp.status_code, "body": body}
                err = None
            except (httpx.HTTPError, PolicyBlocked) as exc:
                result = {"status": None, "body": "", "error": type(exc).__name__}
                err = str(exc) or type(exc).__name__
        await arecord(
            CallRecord("robots", robots_url, "ok" if err is None else "error", result.get("status"), t["ms"], error=err)
        )
        return result

    def _apply_crawl_delay(self, host: str, parser: Protego) -> None:
        if host in self._crawl_delay_applied:
            return
        self._crawl_delay_applied.add(host)
        delay = parser.crawl_delay(self.cfg.settings.robots_token)
        if delay:
            rpm = 60.0 / float(delay)
            if rpm < self.limiter.default_rpm:
                self.limiter.configure(f"domain:{host}", rpm)

    # --- fetch -------------------------------------------------------------------------------------

    async def fetch(self, url: str, *, use_cache: bool = True, allow_fallbacks: bool = True) -> FetchResult:
        url = url.strip()
        if "://" not in url:
            url = "https://" + url
        url = ascii_url(url)  # non-ASCII paths (e.g. a rupee sign in a news slug) must be percent-encoded
        if is_blocked_by_policy(url):
            await arecord(CallRecord("policy", url, "blocked"))
            return FetchResult(url=url, fetched_at=_now(), outcome="blocked_by_policy",
                               error="LinkedIn is never fetched (search snippets only)")
        if use_cache:
            cached = self.fetch_cache.get("page", url)
            if cached is not None:
                result = FetchResult.model_validate(cached)
                return result.model_copy(update={"from_cache": True})

        robots = await self.robots_decision(url)
        if not robots.allowed:
            return FetchResult(url=url, fetched_at=_now(), outcome="blocked_by_robots", robots=robots, error=robots.reason)

        result = await self._fetch_httpx(url, robots)
        if allow_fallbacks and result.outcome in ("ok", "error") and (
            result.outcome == "error" or looks_js_rendered(result.html or "", result.text or "", self.min_text_chars)
        ):
            browser = await self._fetch_playwright(url, robots)
            result = _better(result, browser)
            if len(result.text or "") < self.min_text_chars:
                result = _better(result, await self._fetch_jina(url, robots))

        if result.outcome == "ok":
            self.fetch_cache.set("page", url, result.model_dump(mode="json"))
        return result

    async def get_raw(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        json_body: Any = None,
        provider: str | None = None,
        use_cache: bool = True,
        max_bytes: int | None = None,
    ) -> RawResponse:
        """Polite non-HTML request (APIs, feeds): policy check, robots.txt, cache, rate limit, retry, logging.
        Auth headers are never part of the cache key or the logged endpoint."""
        if is_blocked_by_policy(url):
            await arecord(CallRecord("policy", url, "blocked"))
            raise PolicyBlocked(f"policy: never fetch {host_of(url)}")
        robots = await self.robots_decision(url)
        if not robots.allowed:
            raise RobotsDisallowed(f"{url}: {robots.reason}")
        key = {"method": method, "url": url, "params": params, "body": json_body}
        if use_cache:
            cached = self.fetch_cache.get("raw", key)
            if cached is not None:
                return RawResponse.model_validate(cached)
        limit_key = f"provider:{provider}" if provider else f"domain:{host_of(url)}"
        cap = max_bytes or self.max_bytes

        async def attempt() -> tuple[httpx.Response, bytes]:
            async with self.limiter.limit(limit_key):
                async with self.client.stream(method, url, params=params, headers=headers, json=json_body) as resp:
                    if resp.status_code in RETRYABLE_STATUS:
                        retry_after = parse_retry_after(resp.headers.get("retry-after"))
                        if retry_after:
                            self.limiter.pause(limit_key, retry_after)
                        raise RetryableError(f"HTTP {resp.status_code}", retry_after, resp.status_code)
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > cap:
                            break
                        chunks.append(chunk)
                    return resp, b"".join(chunks)

        with timed() as t:
            try:
                resp, body = await retry_async(attempt, attempts=self.max_retries, base=self.backoff_base, cap=self.backoff_cap)
            except (httpx.HTTPError, RetryableError, PolicyBlocked) as exc:
                await arecord(CallRecord(provider or "http", url, "error", getattr(exc, "status", None), t["ms"], error=str(exc) or type(exc).__name__))
                raise
        await arecord(CallRecord(provider or "http", url, "ok" if resp.is_success else f"http_{resp.status_code}", resp.status_code, t["ms"]))
        text = body.decode(resp.encoding or "utf-8", errors="replace")
        if not resp.is_success:
            raise HttpStatusError(resp.status_code, url, text)
        raw = RawResponse(url=url, final_url=str(resp.url), status=resp.status_code,
                          content_type=resp.headers.get("content-type", ""), text=text)
        if use_cache:
            self.fetch_cache.set("raw", key, raw.model_dump())
        return raw

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        import json

        raw = await self.get_raw(url, **kwargs)
        try:
            return json.loads(raw.text)
        except json.JSONDecodeError as exc:
            # Don't keep a non-JSON body (e.g. an API error message) in the cache.
            self.fetch_cache.delete("raw", {"method": kwargs.get("method", "GET"), "url": url,
                                            "params": kwargs.get("params"), "body": kwargs.get("json_body")})
            raise ValueError(f"non-JSON response from {url}: {raw.text[:200]}") from exc

    async def _fetch_httpx(self, url: str, robots: RobotsDecision) -> FetchResult:
        host = host_of(url)

        async def attempt() -> tuple[httpx.Response, bytes]:
            async with self.limiter.limit(f"domain:{host}"):
                async with self.client.stream("GET", url) as resp:
                    if resp.status_code in RETRYABLE_STATUS:
                        retry_after = parse_retry_after(resp.headers.get("retry-after"))
                        if retry_after:
                            self.limiter.pause(f"domain:{host}", retry_after)
                        raise RetryableError(f"HTTP {resp.status_code}", retry_after, resp.status_code)
                    chunks, size = [], 0
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            break
                        chunks.append(chunk)
                    return resp, b"".join(chunks)

        with timed() as t:
            try:
                resp, body = await retry_async(
                    attempt, attempts=self.max_retries, base=self.backoff_base, cap=self.backoff_cap
                )
            except PolicyBlocked as exc:
                await arecord(CallRecord("http", url, "blocked", latency_ms=t["ms"], error=str(exc)))
                return FetchResult(url=url, fetched_at=_now(), outcome="blocked_by_policy", robots=robots, error=str(exc))
            except (httpx.HTTPError, RetryableError) as exc:
                status = getattr(exc, "status", None)
                msg = str(exc) or type(exc).__name__
                await arecord(CallRecord("http", url, "error", status, t["ms"], error=msg))
                return FetchResult(url=url, status=status, fetched_at=_now(), outcome="error", robots=robots, error=msg)

        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        await arecord(CallRecord("http", url, "ok" if resp.is_success else f"http_{resp.status_code}", resp.status_code, t["ms"]))
        final_url = str(resp.url)
        if not resp.is_success:
            return FetchResult(url=url, final_url=final_url, status=resp.status_code, fetched_at=_now(),
                               method="httpx", outcome="error", robots=robots, error=f"HTTP {resp.status_code}")
        if ctype and not ctype.startswith(HTML_TYPES):
            return FetchResult(url=url, final_url=final_url, status=resp.status_code, fetched_at=_now(),
                               method="httpx", outcome="not_html", robots=robots, error=f"content-type {ctype}")
        encoding = resp.encoding or "utf-8"
        html = body.decode(encoding, errors="replace")
        text = await asyncio.to_thread(extract_text, html, final_url)
        return FetchResult(url=url, final_url=final_url, status=resp.status_code, html=html, text=text,
                           fetched_at=_now(), method="httpx", outcome="ok", robots=robots)

    async def _browser_instance(self) -> Any:
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(headless=True)
        return self._browser

    async def _fetch_playwright(self, url: str, robots: RobotsDecision) -> FetchResult:
        with timed() as t:
            try:
                browser = await self._browser_instance()
                context = await browser.new_context(user_agent=self.user_agent, locale="en-IN")
                try:
                    async def route(r: Any) -> None:
                        if is_blocked_by_policy(r.request.url) or r.request.resource_type in ("image", "media", "font"):
                            await r.abort()
                        else:
                            await r.continue_()

                    await context.route("**/*", route)
                    page = await context.new_page()
                    async with self.limiter.limit(f"domain:{host_of(url)}"):
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.playwright_timeout_ms)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=10_000)
                        except Exception:  # pages with long-polling never go idle; keep what rendered
                            pass
                    html = await page.content()
                    final_url = page.url
                    status = resp.status if resp else None
                finally:
                    await context.close()
            except Exception as exc:
                msg = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                await arecord(CallRecord("playwright", url, "error", latency_ms=t["ms"], error=msg))
                return FetchResult(url=url, fetched_at=_now(), method="playwright", outcome="error", robots=robots, error=msg)
        await arecord(CallRecord("playwright", url, "ok", status, t["ms"]))
        text = await asyncio.to_thread(extract_text, html, final_url)
        return FetchResult(url=url, final_url=final_url, status=status, html=html, text=text,
                           fetched_at=_now(), method="playwright", outcome="ok", robots=robots)

    async def _fetch_jina(self, url: str, robots: RobotsDecision) -> FetchResult:
        headers = {"Accept": "text/plain", "X-Return-Format": "text"}
        if self.cfg.settings.jina_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.settings.jina_api_key.get_secret_value()}"
        with timed() as t:
            try:
                async with self.limiter.limit("provider:jina"):
                    resp = await self.client.get(f"https://r.jina.ai/{url}", headers=headers)
            except (httpx.HTTPError, PolicyBlocked) as exc:
                msg = str(exc) or type(exc).__name__
                await arecord(CallRecord("jina", url, "error", latency_ms=t["ms"], error=msg))
                return FetchResult(url=url, fetched_at=_now(), method="jina", outcome="error", robots=robots, error=msg)
        await arecord(CallRecord("jina", url, "ok" if resp.is_success else f"http_{resp.status_code}", resp.status_code, t["ms"]))
        if not resp.is_success:
            return FetchResult(url=url, status=resp.status_code, fetched_at=_now(), method="jina", outcome="error",
                               robots=robots, error=f"HTTP {resp.status_code}")
        return FetchResult(url=url, final_url=url, status=resp.status_code, text=resp.text.strip(),
                           fetched_at=_now(), method="jina", outcome="ok", robots=robots)


def _better(a: FetchResult, b: FetchResult) -> FetchResult:
    """Prefer a successful result with more extracted text."""
    if b.outcome != "ok":
        return a
    if a.outcome != "ok":
        return b
    return b if len(b.text or "") > len(a.text or "") else a


async def fetch(url: str, **kwargs: Any) -> FetchResult:
    """One-off fetch with a short-lived Fetcher. Pipelines should share one Fetcher instead."""
    async with Fetcher() as fetcher:
        return await fetcher.fetch(url, **kwargs)
