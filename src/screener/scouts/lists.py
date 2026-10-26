"""Curated lists (HolonIQ, GSV Cup, Indian "top edtech startups"): found by search, then extracted."""

from __future__ import annotations

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.scouts.pages import scout_page
from screener.urls import host_of


class ListsScout(BaseScout):
    name = "lists"
    config_key = "lists"
    needs_llm = True

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        exclude = self.conf.get("exclude_domains", [])
        per_query = int(self.conf.get("pages_per_query", 2))
        out: list[DiscoveryItem] = []
        stats: dict[str, int] = {}
        visited: set[str] = set()
        for query in self.conf.get("queries", []):
            if len(out) >= limit:
                break
            res = await self.ctx.router.search(query, strategy="cheap", n=10)
            pages = [
                r.url for r in res.results
                if not any(host_of(r.url) == d or host_of(r.url).endswith("." + d) for d in exclude) and r.url not in visited
            ][:per_query]
            for url in pages:
                visited.add(url)
                found = await scout_page(self, url, query, limit - len(out), stats)
                self.note(f"'{query}': {len(found)} from {url}")
                out += found
                if len(out) >= limit:
                    break
        if stats:
            self.note(f"dropped: {stats}")
        return out
