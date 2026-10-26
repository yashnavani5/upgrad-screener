"""Portfolios of edtech-focused and India-focused investors; education companies only."""

from __future__ import annotations

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.scouts.pages import scout_named_pages


class VcPortfoliosScout(BaseScout):
    name = "vc_portfolios"
    config_key = "vc_portfolios"
    needs_llm = True

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        return await scout_named_pages(self, list(self.conf or []), limit)
