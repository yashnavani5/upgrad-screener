"""Log every external call (provider, status, latency) to the log and, when the DB is reachable, api_calls."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("screener.calls")

current_run_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar("current_run_id", default=None)


@dataclass
class CallRecord:
    provider: str
    endpoint: str | None = None
    status: str = "ok"
    http_status: int | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    cache_hit: bool = False
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _persist(rec: CallRecord) -> None:
    from screener.db import try_session
    from screener.models import ApiCall

    with try_session() as session:
        if session is None:
            return
        session.add(
            ApiCall(
                run_id=current_run_id.get(),
                provider=rec.provider,
                endpoint=(rec.endpoint or "")[:2000] or None,
                status=rec.status,
                http_status=rec.http_status,
                latency_ms=rec.latency_ms,
                tokens_in=rec.tokens_in,
                tokens_out=rec.tokens_out,
                cost_usd=rec.cost_usd,
                cache_hit=rec.cache_hit,
                error=(rec.error or "")[:2000] or None,
            )
        )


def record(rec: CallRecord) -> None:
    log.info(
        "call provider=%s status=%s http=%s latency_ms=%s cache=%s endpoint=%s%s",
        rec.provider,
        rec.status,
        rec.http_status,
        rec.latency_ms,
        rec.cache_hit,
        rec.endpoint,
        f" error={rec.error}" if rec.error else "",
    )
    try:
        _persist(rec)
    except Exception as exc:  # telemetry must never break a run
        log.debug("api_calls insert failed: %s", exc)


async def arecord(rec: CallRecord) -> None:
    await asyncio.to_thread(record, rec)


@contextmanager
def timed() -> Iterator[dict[str, int]]:
    """with timed() as t: ...; t['ms'] holds elapsed milliseconds afterwards."""
    box = {"ms": 0}
    start = time.perf_counter()
    try:
        yield box
    finally:
        box["ms"] = int((time.perf_counter() - start) * 1000)
