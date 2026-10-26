"""Shared flow for portfolio / cohort / list pages: find by search, fetch politely, extract with Gemini."""

from __future__ import annotations

from typing import Any

from screener.scouts.base import BaseScout, DiscoveryItem, find_page
from screener.scouts.extract import extract_companies, page_text_and_links


async def scout_page(scout: BaseScout, url: str, label: str, limit: int, stats: dict[str, int]) -> list[DiscoveryItem]:
    result = await scout.ctx.fetcher.fetch(url)
    if not result.ok:
        scout.note(f"{label}: fetch {result.outcome} ({result.error})")
        return []
    if result.html:
        text, links = page_text_and_links(result.html, result.final_url or url)
    else:  # Jina Reader text has no HTML/links
        text, links = result.text or "", []
    if len(text) < 200:
        scout.note(f"{label}: page has too little text ({len(text)} chars)")
        return []
    return await extract_companies(
        scout.ctx.gemini, text, links, source_url=result.final_url or url, scout=scout.name,
        context=label, education_only=True, limit=limit, stats=stats,
    )


async def scout_named_pages(scout: BaseScout, entries: list[dict[str, Any]], limit: int) -> list[DiscoveryItem]:
    out: list[DiscoveryItem] = []
    stats: dict[str, int] = {}
    for entry in entries:
        if len(out) >= limit:
            break
        url = entry.get("url") or await find_page(scout.ctx.router, entry["query"], entry.get("domains", []))
        if not url:
            scout.note(f"{entry['name']}: no page found on {entry.get('domains')}")
            continue
        found = await scout_page(scout, url, entry["name"], limit - len(out), stats)
        scout.note(f"{entry['name']}: {len(found)} education companies from {url}")
        out += found
    if stats:
        scout.note(f"dropped: {stats}")
    return out
