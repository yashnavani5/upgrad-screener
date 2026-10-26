"""`screener sources resolve-feeds`: discover each news site's RSS/Atom feed from its HTML, verify it parses
with entries, and save the verified list to config/sources.yaml (comments preserved)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import lxml.html

from screener.http import Fetcher

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+xml", "text/xml")


def feed_links(html: str, base_url: str) -> list[str]:
    """<link rel="alternate" type="...rss/atom..."> first, then anchors that look like feeds."""
    doc = lxml.html.fromstring(html)
    doc.make_links_absolute(base_url, resolve_base_href=True)
    found: list[str] = []
    for link in doc.xpath('//link[@rel="alternate"][@href]'):
        if (link.get("type") or "").lower() in FEED_TYPES:
            found.append(link.get("href"))
    for a in doc.xpath("//a[@href]"):
        href = a.get("href")
        if any(k in href.lower() for k in ("/feed", "/rss", "rss.xml", "feed.xml", "atom.xml")):
            found.append(href)
    return list(dict.fromkeys(found))


def rank_candidates(candidates: list[str], site_url: str) -> list[str]:
    """Feeds under the configured site path first (e.g. a /tag/india/ page's own feed before the global one)."""
    prefix = site_url.rstrip("/")
    return sorted(candidates, key=lambda u: (not u.startswith(prefix + "/"), candidates.index(u)))


async def _try_feed(fetcher: Fetcher, url: str) -> tuple[int, str | None]:
    """(entries, html_if_not_a_feed)."""
    raw = await fetcher.get_raw(url, use_cache=False)
    parsed = feedparser.parse(raw.text)
    if parsed.entries:
        return len(parsed.entries), None
    return 0, raw.text if "html" in raw.content_type.lower() else None


async def resolve_feeds(fetcher: Fetcher, sites: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    resolved, notes = [], []
    for site in sites:
        page = await fetcher.fetch(site["site"], allow_fallbacks=False)
        if not page.ok or not page.html:
            notes.append(f"{site['name']}: site fetch {page.outcome} ({page.error})")
            continue
        candidates = rank_candidates(feed_links(page.html, page.final_url or site["site"]), site["site"])
        chosen = None
        queue = [(u, 0) for u in candidates[:6]]
        while queue and chosen is None:
            url, depth = queue.pop(0)
            try:
                entries, html_page = await _try_feed(fetcher, url)
            except Exception as exc:
                notes.append(f"{site['name']}: {url} -> {type(exc).__name__}")
                continue
            if entries:
                chosen = {"name": site["name"], "site": site["site"], "feed_url": url,
                          "entries": entries, "verified_at": datetime.now(timezone.utc).date().isoformat()}
            elif html_page and depth == 0:
                # An "RSS" index page: follow one level to the feeds it lists.
                queue += [(u, 1) for u in rank_candidates(feed_links(html_page, url), site["site"])[:6] if u != url]
        if chosen:
            resolved.append(chosen)
            notes.append(f"{site['name']}: {chosen['feed_url']} ({chosen['entries']} entries)")
        else:
            notes.append(f"{site['name']}: no parseable feed among {len(candidates)} candidate links")
    return resolved, notes


def save_feeds(path: Path, feeds: list[dict[str, Any]]) -> None:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 140
    data = yaml.load(path.read_text(encoding="utf-8"))
    seq = CommentedSeq()
    for f in feeds:
        m = CommentedMap(f)
        m.fa.set_flow_style()
        seq.append(m)
    data["news_rss"]["feeds"] = seq
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
