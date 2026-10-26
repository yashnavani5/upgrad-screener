"""Scout interface, shared context, concurrent runner and persistence to the discoveries table."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from screener.config import AppConfig
from screener.http import Fetcher
from screener.scouts.prefilter import Prefilter
from screener.search import SearchRouter
from screener.urls import dedupe_key as url_key
from screener.urls import host_of

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiscoveryItem(BaseModel):
    scout: str
    raw_company_name: str
    raw_person_name: str | None = None
    company_url: str | None = None
    person_url: str | None = None
    source_url: str
    snippet: str | None = None
    evidence_span: str | None = None
    discovered_at: datetime = Field(default_factory=utcnow)
    extra: dict[str, Any] = Field(default_factory=dict)

    def dedupe_key(self) -> str:
        parts = [self.scout, url_key(self.source_url), self.raw_company_name.casefold().strip(),
                 (self.raw_person_name or "").casefold().strip()]
        return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


@dataclass
class ScoutContext:
    cfg: AppConfig
    fetcher: Fetcher
    router: SearchRouter
    http: httpx.AsyncClient
    gemini: Any = None  # GeminiClient | None

    @property
    def sources(self) -> dict[str, Any]:
        return self.cfg.sources

    @property
    def prefilter(self) -> Prefilter:
        return Prefilter(self.sources.get("prefilter_keywords", []))


class BaseScout(ABC):
    name: ClassVar[str]
    config_key: ClassVar[str]
    needs_llm: ClassVar[bool] = False

    def __init__(self, ctx: ScoutContext) -> None:
        self.ctx = ctx
        self.conf: Any = ctx.sources.get(self.config_key, {})
        self.notes: list[str] = []

    def enabled(self) -> tuple[bool, str]:
        if self.needs_llm and self.ctx.gemini is None:
            return False, "needs Gemini (set GEMINI_API_KEY and GEMINI_MODEL_FAST)"
        return True, "ok"

    def note(self, message: str) -> None:
        log.info("[%s] %s", self.name, message)
        self.notes.append(message)

    @abstractmethod
    async def discover(self, limit: int) -> list[DiscoveryItem]: ...


async def find_page(router: SearchRouter, query: str, domains: list[str]) -> str | None:
    """First search result on one of the expected domains (verifies the hint at run time)."""
    def on_domain(url: str) -> bool:
        host = host_of(url)
        return any(host == d or host.endswith("." + d) for d in domains)

    for q in (query, f"{query} site:{domains[0]}" if domains else None):
        if not q:
            continue
        res = await router.search(q, strategy="cheap", n=10)
        for r in res.results:
            if on_domain(r.url):
                return r.url
    return None


@dataclass
class ScoutReport:
    name: str
    enabled: bool
    reason: str
    items: list[DiscoveryItem] = field(default_factory=list)
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    inserted: int = 0


def _dedupe(items: list[DiscoveryItem]) -> list[DiscoveryItem]:
    seen: set[str] = set()
    out = []
    for it in items:
        k = it.dedupe_key()
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


async def run_scout(scout: BaseScout, limit: int) -> ScoutReport:
    ok, reason = scout.enabled()
    report = ScoutReport(scout.name, ok, reason)
    if not ok:
        return report
    start = time.perf_counter()
    try:
        report.items = _dedupe(await scout.discover(limit))[:limit]
    except Exception as exc:  # fail soft: one scout never stops the others
        log.exception("scout %s failed", scout.name)
        report.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    report.notes = scout.notes
    report.seconds = time.perf_counter() - start
    return report


async def run_scouts(scouts: list[BaseScout], limit: int) -> list[ScoutReport]:
    return list(await asyncio.gather(*(run_scout(s, limit) for s in scouts)))


def persist(items: list[DiscoveryItem], batch_id: uuid.UUID) -> int:
    """Insert discoveries, skipping ones already stored (same dedupe key). Returns rows inserted."""
    if not items:
        return 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from screener.db import get_engine, is_postgres
    from screener.models import Discovery

    engine = get_engine()
    insert = pg_insert if is_postgres(engine) else sqlite_insert
    rows = [
        {
            "id": uuid.uuid4(), "batch_id": batch_id, "scout": it.scout,
            "raw_company_name": it.raw_company_name[:300], "raw_person_name": (it.raw_person_name or None) and it.raw_person_name[:300],
            "company_url": it.company_url, "person_url": it.person_url, "source_url": it.source_url,
            "snippet": it.snippet, "evidence_span": it.evidence_span, "extra": it.extra,
            "dedupe_key": it.dedupe_key(), "discovered_at": it.discovered_at, "created_at": utcnow(),
        }
        for it in items
    ]
    table = Discovery.__table__
    stmt = insert(table).values(rows).on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(table.c.id)
    with engine.begin() as conn:
        # Count RETURNING rows: psycopg does not report rowcount for multi-row INSERT .. ON CONFLICT.
        return len(conn.execute(stmt).all())
