from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from screener.ratelimit import (
    RateLimiter,
    RetryableError,
    TokenBucket,
    backoff_delay,
    parse_retry_after,
    retry_async,
)


class VirtualTime:
    """Deterministic clock + sleep: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_bucket_allows_burst_then_waits_for_refill() -> None:
    vt = VirtualTime()
    bucket = TokenBucket(rate=2.0, capacity=2.0, clock=vt.clock, sleep=vt.sleep)
    assert await bucket.acquire() == 0
    assert await bucket.acquire() == 0
    waited = await bucket.acquire()
    assert waited == pytest.approx(0.5)
    assert vt.now == pytest.approx(0.5)


async def test_bucket_rate_over_many_calls() -> None:
    vt = VirtualTime()
    bucket = TokenBucket(rate=1.0, capacity=1.0, clock=vt.clock, sleep=vt.sleep)
    for _ in range(5):
        await bucket.acquire()
    # first is free, the next four wait one second each
    assert vt.now == pytest.approx(4.0)


async def test_pause_delays_next_acquire() -> None:
    vt = VirtualTime()
    bucket = TokenBucket(rate=1.0, capacity=1.0, clock=vt.clock, sleep=vt.sleep)
    bucket.pause(10.0)
    await bucket.acquire()
    assert vt.now >= 10.0


async def test_limiter_keys_are_independent() -> None:
    vt = VirtualTime()
    limiter = RateLimiter(default_rpm=60, max_concurrency=4, clock=vt.clock, sleep=vt.sleep)
    async with limiter.limit("domain:a.example"):
        pass
    async with limiter.limit("domain:b.example"):
        pass
    assert vt.now == 0.0  # different keys, no waiting
    async with limiter.limit("domain:a.example"):
        pass
    assert vt.now == pytest.approx(1.0)  # 60 rpm = one per second on the same key


async def test_configure_overrides_rate() -> None:
    vt = VirtualTime()
    limiter = RateLimiter(default_rpm=60, clock=vt.clock, sleep=vt.sleep)
    limiter.configure("provider:slow", 6)
    async with limiter.limit("provider:slow"):
        pass
    async with limiter.limit("provider:slow"):
        pass
    assert vt.now == pytest.approx(10.0)


async def test_global_concurrency_cap() -> None:
    limiter = RateLimiter(default_rpm=60_000, max_concurrency=2)
    active = peak = 0

    async def job(i: int) -> None:
        nonlocal active, peak
        async with limiter.limit(f"key{i}"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(job(i) for i in range(8)))
    assert peak == 2


def test_parse_retry_after_seconds_and_date() -> None:
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after(" 1.5 ") == 1.5
    now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    when = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(when, now=now) == pytest.approx(30.0)
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after(format_datetime(now - timedelta(seconds=30), usegmt=True), now=now) == 0.0


def test_backoff_is_jittered_and_capped() -> None:
    rng = random.Random(7)
    for attempt in range(10):
        d = backoff_delay(attempt, base=1.0, cap=8.0, rng=rng)
        assert 0.0 <= d <= min(8.0, 2**attempt)


async def test_retry_honors_retry_after() -> None:
    vt = VirtualTime()
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RetryableError("429", retry_after=7.0, status=429)
        return "ok"

    assert await retry_async(flaky, attempts=3, sleep=vt.sleep) == "ok"
    assert vt.sleeps == [7.0, 7.0]


async def test_retry_uses_backoff_without_retry_after_and_gives_up() -> None:
    vt = VirtualTime()

    async def always() -> None:
        raise RetryableError("503", status=503)

    with pytest.raises(RetryableError):
        await retry_async(always, attempts=4, base=1.0, cap=60.0, sleep=vt.sleep, rng=random.Random(1))
    assert len(vt.sleeps) == 3
    for i, s in enumerate(vt.sleeps):
        assert 0 <= s <= 2**i


async def test_non_retryable_errors_propagate_immediately() -> None:
    vt = VirtualTime()

    async def bad() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await retry_async(bad, attempts=5, sleep=vt.sleep)
    assert vt.sleeps == []
