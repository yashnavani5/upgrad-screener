"""GDELT DOC 2.0 API (mode=artlist, format=json); titles are classified by Gemini.

We request `months` of coverage via startdatetime/enddatetime, but the DOC API only returns roughly the
last 3 months. GDELT throttles hard, so requests are spaced (rpm) and a 429 waits before one retry."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from screener.ratelimit import RetryableError
from screener.scouts.base import BaseScout, DiscoveryItem
from screener.scouts.extract import NewsInput, classify_news

GDELT_TIME = "%Y%m%d%H%M%S"


class GdeltScout(BaseScout):
    name = "gdelt"
    config_key = "gdelt"
    needs_llm = True

    async def _query(self, query: str, start: datetime, end: datetime) -> list[dict]:
        params = {
            "query": query, "mode": "artlist", "format": "json", "sort": "datedesc",
            "maxrecords": int(self.conf.get("max_records", 250)),
            "startdatetime": start.strftime(GDELT_TIME), "enddatetime": end.strftime(GDELT_TIME),
        }
        for attempt in range(2):
            try:
                data = await self.ctx.fetcher.get_json(self.conf["endpoint"], params=params, provider="gdelt")
                return data.get("articles", []) or []
            except RetryableError:
                if attempt == 0:
                    await asyncio.sleep(float(self.conf.get("retry_wait_seconds", 30)))
                    continue
                raise
        return []

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        self.ctx.fetcher.limiter.configure("provider:gdelt", float(self.conf.get("rpm", 4)))
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30 * int(self.conf.get("months", 24)))
        items: list[NewsInput] = []
        seen: set[str] = set()
        oldest: str | None = None
        for query in self.conf.get("queries", []):
            try:
                articles = await self._query(query, start, end)
            except Exception as exc:
                self.note(f"'{query}': {type(exc).__name__}: {str(exc)[:120]}")
                continue
            for art in articles:
                url, title = art.get("url"), (art.get("title") or "").strip()
                if not url or url in seen or art.get("language", "English") != "English":
                    continue
                seen.add(url)
                oldest = min(filter(None, [oldest, art.get("seendate")]), default=None)
                if self.ctx.prefilter.matches(title):
                    items.append(NewsInput(item_id=len(items), title=title, text="", url=url))
        self.note(f"{len(seen)} English articles (oldest {oldest}), {len(items)} passed the prefilter")
        stats: dict[str, int] = {}
        found = await classify_news(self.ctx.gemini, items, self.name, limit=limit, stats=stats)
        if stats:
            self.note(f"dropped: {stats}")
        return found
