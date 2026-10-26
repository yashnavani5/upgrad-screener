"""News: verified RSS feeds from config + SearXNG news-category searches, keyword prefilter, then Gemini-fast
batch classification (10 items per call) into company / founders / event type."""

from __future__ import annotations

import html
import re

import feedparser

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.scouts.extract import NewsInput, classify_news
from screener.search import SearxngProvider


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


class NewsRssScout(BaseScout):
    name = "news_rss"
    config_key = "news_rss"
    needs_llm = True

    async def _feed_items(self) -> list[tuple[str, str, str]]:
        feeds = self.conf.get("feeds") or []
        if not feeds:
            self.note("no verified feeds yet: run `screener sources resolve-feeds`")
        out = []
        for feed in feeds:
            try:
                raw = await self.ctx.fetcher.get_raw(feed["feed_url"])
            except Exception as exc:
                self.note(f"feed {feed.get('name')}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            parsed = feedparser.parse(raw.text)
            for e in parsed.entries:
                link = e.get("link")
                if link:
                    out.append((e.get("title", ""), _strip_html(e.get("summary", "")), link))
        return out

    async def _news_search_items(self) -> list[tuple[str, str, str]]:
        provider = self.ctx.router.providers.get("searxng")
        if not isinstance(provider, SearxngProvider):
            self.note("SearXNG not configured: news searches skipped")
            return []
        out = []
        for q in self.conf.get("news_queries", []):
            try:
                results = await provider.search(q, 20, categories="news", time_range="year")
            except Exception as exc:
                self.note(f"news search '{q}': {type(exc).__name__}")
                continue
            out += [(r.title, r.snippet, r.url) for r in results]
        return out

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        raw = await self._feed_items() + await self._news_search_items()
        seen, items = set(), []
        for title, text, url in raw:
            if url in seen or not self.ctx.prefilter.matches(title, text):
                continue
            seen.add(url)
            items.append(NewsInput(item_id=len(items), title=title, text=text, url=url))
        self.note(f"{len(raw)} news items, {len(items)} passed the education prefilter")
        stats: dict[str, int] = {}
        found = await classify_news(self.ctx.gemini, items, self.name,
                                    batch_size=int(self.conf.get("batch_size", 10)), limit=limit, stats=stats)
        if stats:
            self.note(f"dropped: {stats}")
        return found
