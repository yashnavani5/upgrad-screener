"""Fetch and prepare source pages. The same text derivation is used by the extractor and the verifier."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import lxml.html
import trafilatura

from screener.http import FetchResult, Fetcher
from screener.resolve.normalize import registered_domain
from screener.scouts.extract import page_text_and_links
from screener.urls import is_blocked_by_policy

log = logging.getLogger(__name__)

TextMode = Literal["full", "article"]
PAGE_TIMEOUT_SECONDS = 90


@dataclass
class Page:
    url: str
    text: str
    mode: TextMode
    fetched_at: datetime
    title: str = ""
    published: str | None = None
    links: list[tuple[str, str]] | None = None


def html_title(html: str) -> str:
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return ""
    return re.sub(r"\s+", " ", " ".join(doc.xpath("//title//text()"))).strip()


def published_line(date: str | None) -> str:
    return f"Published: {date[:10]} (page metadata)" if date else ""


def page_text(result: FetchResult, mode: TextMode) -> str:
    """'full' keeps every block (team/about/careers cards); 'article' is trafilatura's main text. Title first,
    then the publication date from the page's own HTML metadata, so the extractor, the grounding check and
    the verifier all see the same date (it is otherwise invisible in body text)."""
    if result.html:
        title = html_title(result.html)
        if mode == "full":
            body, _ = page_text_and_links(result.html, result.final_url or result.url)
        else:
            body = result.text or ""
        published = published_line(published_date(result.html, result.final_url or result.url))
        return "\n".join(p for p in (title, published, body) if p).strip()
    return (result.text or "").strip()  # Jina Reader output has no HTML


def published_date(html: str | None, url: str) -> str | None:
    if not html:
        return None
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        return None
    return getattr(meta, "date", None) if meta else None


async def fetch_page(fetcher: Fetcher, url: str, mode: TextMode, *, fresh: bool = False,
                     fallbacks: bool = True) -> Page | None:
    """`fallbacks=False`: plain HTTP only (no Playwright / Jina), for high-volume light checks."""
    if is_blocked_by_policy(url):
        return None
    try:  # hard cap per page (slow-drip servers, Playwright) so one page can't stall a run
        result = await asyncio.wait_for(fetcher.fetch(url, use_cache=not fresh, allow_fallbacks=fallbacks),
                                        timeout=PAGE_TIMEOUT_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        return None
    except Exception as exc:  # fail soft: one odd page (bad encoding, broken redirect) never stops a stage
        log.warning("fetch_page %s: %s: %s", url, type(exc).__name__, str(exc)[:200])
        return None
    if not result.ok:
        return None
    text = page_text(result, mode)
    if len(text) < 80:
        return None
    links = page_text_and_links(result.html, result.final_url or url)[1] if result.html else []
    return Page(url=result.final_url or url, text=text, mode=mode, fetched_at=result.fetched_at,
                title=html_title(result.html) if result.html else "", published=published_date(result.html, url),
                links=links)


def site_links(home: Page, domain: str, keywords: dict[str, list[str]], limit: int) -> list[tuple[str, str]]:
    """[(kind, url)] for about/team/careers links on the company's own registrable domain."""
    picked: dict[str, str] = {}
    for text, url in home.links or []:
        if registered_domain(url) != domain:
            continue
        hay = f"{text} {urlsplit(url).path}".lower()
        for kind, words in keywords.items():
            if kind not in picked and any(w in hay for w in words):
                picked[kind] = url
    return list(picked.items())[:limit]


def window_text(text: str, anchors: list[str], max_chars: int) -> str:
    """Text for the verifier: whole page if short, else windows around where anchors occur (merged)."""
    if len(text) <= max_chars:
        return text
    low = text.lower()
    spans: list[tuple[int, int]] = []
    for a in anchors:
        i = low.find(a.lower()[:60]) if a else -1
        if i >= 0:
            spans.append((max(0, i - 1500), min(len(text), i + len(a) + 1500)))
    if not spans:
        return text[:max_chars]
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out = "\n...\n".join(text[s:e] for s, e in merged)
    return out[:max_chars]

