"""Async token buckets per key (domain or provider), a global concurrency cap, and retry with backoff."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, TypeVar

T = TypeVar("T")

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """`rate` tokens per second, holding at most `capacity`. acquire() waits until a token is free."""

    def __init__(self, rate: float, capacity: float = 1.0, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, tokens: float = 1.0) -> float:
        """Take `tokens`; returns seconds waited."""
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                wait = (tokens - self._tokens) / self.rate
                await self._sleep(wait)
                waited += wait

    def pause(self, seconds: float) -> None:
        """Server said back off (Retry-After): drain the bucket so the next acquire waits `seconds`."""
        self._refill()
        self._tokens = min(self._tokens, 0.0) - seconds * self.rate


class RateLimiter:
    """Registry of per-key buckets plus one global semaphore."""

    def __init__(
        self,
        default_rpm: float = 20.0,
        max_concurrency: int = 8,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.default_rpm = default_rpm
        self._rpm: dict[str, float] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._sleep = sleep

    def configure(self, key: str, rpm: float) -> None:
        self._rpm[key] = rpm
        self._buckets.pop(key, None)

    def bucket(self, key: str) -> TokenBucket:
        if key not in self._buckets:
            rpm = self._rpm.get(key, self.default_rpm)
            self._buckets[key] = TokenBucket(rpm / 60.0, 1.0, clock=self._clock, sleep=self._sleep)
        return self._buckets[key]

    @asynccontextmanager
    async def limit(self, key: str) -> AsyncIterator[None]:
        await self.bucket(key).acquire()
        async with self._semaphore:
            yield

    def pause(self, key: str, seconds: float) -> None:
        self.bucket(key).pause(seconds)


class RetryableError(Exception):
    """Raise from a retried call to request another attempt, optionally after `retry_after` seconds."""

    def __init__(self, message: str, retry_after: float | None = None, status: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status = status


RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After is either delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0, rng: random.Random | None = None) -> float:
    """Full-jitter exponential backoff: uniform(0, min(cap, base * 2**attempt))."""
    rng = rng or random
    return rng.uniform(0.0, min(cap, base * (2**attempt)))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base: float = 1.0,
    cap: float = 60.0,
    sleep: Sleep = asyncio.sleep,
    on_retry: Callable[[int, RetryableError, float], None] | None = None,
    rng: random.Random | None = None,
) -> T:
    """Call fn; on RetryableError wait Retry-After (if given) or jittered backoff, up to `attempts` calls."""
    for attempt in range(attempts):
        try:
            return await fn()
        except RetryableError as exc:
            if attempt == attempts - 1:
                raise
            delay = exc.retry_after if exc.retry_after is not None else backoff_delay(attempt, base, cap, rng)
            delay = min(delay, cap)
            if on_retry:
                on_retry(attempt + 1, exc, delay)
            await sleep(delay)
    raise AssertionError("unreachable")


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """Process-wide limiter configured from config/providers.yaml."""
    global _limiter
    if _limiter is None:
        from screener.config import get_config

        p = get_config().providers
        limiter = RateLimiter(
            default_rpm=float(p.get("fetch", {}).get("per_domain_rpm", 20)),
            max_concurrency=int(p.get("http", {}).get("max_concurrency", 8)),
        )
        for name, spec in p.get("search", {}).get("providers", {}).items():
            if spec.get("rpm"):
                limiter.configure(f"provider:{name}", float(spec["rpm"]))
        limiter.configure("provider:jina", float(p.get("fetch", {}).get("jina_rpm", 20)))
        _limiter = limiter
    return _limiter
