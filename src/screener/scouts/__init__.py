"""Discovery scouts. Each yields DiscoveryItem rows; scouts cast a wide net and never judge quality."""

from __future__ import annotations

from screener.scouts.accelerators import AcceleratorsScout
from screener.scouts.base import BaseScout, DiscoveryItem, ScoutContext, ScoutReport, persist, run_scouts
from screener.scouts.betalist import BetalistScout
from screener.scouts.gdelt import GdeltScout
from screener.scouts.github import GithubScout
from screener.scouts.hn import HnScout
from screener.scouts.lists import ListsScout
from screener.scouts.news_rss import NewsRssScout
from screener.scouts.producthunt import ProductHuntScout
from screener.scouts.vc_portfolios import VcPortfoliosScout
from screener.scouts.x import XScout
from screener.scouts.xray import XrayScout

SCOUTS: dict[str, type[BaseScout]] = {
    cls.name: cls
    for cls in (
        NewsRssScout, GdeltScout, AcceleratorsScout, VcPortfoliosScout, ListsScout,
        GithubScout, ProductHuntScout, BetalistScout, HnScout, XrayScout, XScout,
    )
}

__all__ = ["SCOUTS", "BaseScout", "DiscoveryItem", "ScoutContext", "ScoutReport", "persist", "run_scouts"]
