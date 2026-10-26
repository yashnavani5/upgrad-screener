"""LLM extraction for scouts (Gemini fast). The LLM only extracts; every item must carry an evidence_span
copied from the input, and is dropped if the span or the names in it can't be found (see grounding.py)."""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

import lxml.html
from pydantic import BaseModel, Field

from screener.scouts.base import DiscoveryItem
from screener.scouts.grounding import check, name_in_text
from screener.urls import dedupe_key, is_blocked_by_policy, normalize_url

log = logging.getLogger(__name__)

CHUNK_CHARS = 12_000
CHUNK_OVERLAP = 400
MAX_LINKS_PER_CHUNK = 150

EventType = Literal["funding", "acquisition", "launch", "partnership", "hiring", "other"]


# --- news / article classification ----------------------------------------------------------------


class NewsInput(BaseModel):
    item_id: int
    title: str
    text: str = ""
    url: str


class NewsClassification(BaseModel):
    item_id: int
    is_education: bool = Field(description="True only if the main company is an education/edtech/upskilling business.")
    company_name: str | None = Field(None, description="Main company exactly as written in the item; null if none.")
    founders: list[str] = Field(default_factory=list, description="Founder names written in the item text only.")
    event_type: EventType = "other"
    evidence_span: str | None = Field(None, description="Verbatim quote from the item that names the company.")


class NewsBatch(BaseModel):
    items: list[NewsClassification]


NEWS_SYSTEM = (
    "You classify news items for an education-startup discovery pipeline. For each item: decide whether the "
    "main company is an education business (edtech, upskilling, test prep, school/university software, "
    "corporate learning, study abroad, tutoring). Copy the company name exactly as written. List founders "
    "only if their names appear in the item text. evidence_span must be an exact, verbatim quote from the "
    "item's title or text that contains the company name. Never invent names or facts; use null when unsure."
)


def _news_prompt(batch: list[NewsInput]) -> str:
    lines = ["Classify each item. Return one entry per item_id.\n"]
    for it in batch:
        lines.append(f"[item_id={it.item_id}]\nTITLE: {it.title}\nTEXT: {it.text[:1200]}\n")
    return "\n".join(lines)


async def classify_news(
    gemini: Any, items: list[NewsInput], scout: str, *, batch_size: int = 10, limit: int | None = None,
    stats: dict[str, int] | None = None,
) -> list[DiscoveryItem]:
    """Batch-classify news items (10 per call) into grounded discoveries, one row per founder."""
    stats = stats if stats is not None else {}
    out: list[DiscoveryItem] = []
    by_id = {it.item_id: it for it in items}
    for start in range(0, len(items), batch_size):
        if limit is not None and len(out) >= limit:
            break
        batch = items[start : start + batch_size]
        try:
            result = await gemini.generate_json(_news_prompt(batch), NewsBatch, "fast", system=NEWS_SYSTEM)
        except Exception as exc:  # one failed batch never stops the scout
            log.warning("[%s] classification batch failed: %s", scout, exc)
            stats["batch_errors"] = stats.get("batch_errors", 0) + 1
            continue
        for c in result.items:
            src = by_id.get(c.item_id)
            if src is None or not c.is_education or not c.company_name:
                stats["not_education"] = stats.get("not_education", 0) + 1
                continue
            source_text = f"{src.title}\n{src.text}"
            g = check(c.evidence_span, source_text, c.company_name)
            if not g.ok:
                stats["ungrounded"] = stats.get("ungrounded", 0) + 1
                continue
            founders = [f for f in dict.fromkeys(c.founders) if name_in_text(f, source_text)]
            for founder in founders or [None]:
                out.append(DiscoveryItem(
                    scout=scout, raw_company_name=c.company_name.strip(), raw_person_name=founder,
                    source_url=src.url, snippet=src.title, evidence_span=c.evidence_span,
                    extra={"event_type": c.event_type},
                ))
    return out


# --- page (portfolio / list) extraction ------------------------------------------------------------


class PageCompany(BaseModel):
    company_name: str = Field(description="Company name exactly as written on the page.")
    company_url: str | None = Field(None, description="Only a URL copied from the LINKS list; else null.")
    founders: list[str] = Field(default_factory=list, description="Founder names written on the page only.")
    is_education: bool = Field(description="True only if the company is an education/edtech/upskilling business.")
    evidence_span: str = Field(description="Verbatim quote from the page text that contains the company name.")


class PageExtraction(BaseModel):
    companies: list[PageCompany]


PAGE_SYSTEM = (
    "You extract companies from a web page (an investor portfolio, accelerator cohort or startup list) for an "
    "education-startup discovery pipeline. Extract every company listed on the page. Copy names exactly as "
    "written. company_url must be copied from the LINKS list, or null. founders only if named on the page. "
    "is_education is true only for education businesses (edtech, upskilling, test prep, school/university "
    "software, corporate learning, study abroad, tutoring). evidence_span must be an exact, verbatim quote "
    "from PAGE TEXT that contains the company name. Never invent companies, names or URLs."
)

BLOCK_TAGS = ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "section", "article",
              "header", "footer", "br", "dt", "dd", "figcaption", "a", "span", "button")


def page_text_and_links(html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """Plain text with line breaks between blocks (card grids stay readable) and (anchor text, URL) pairs."""
    doc = lxml.html.fromstring(html)
    for bad in doc.xpath("//script|//style|//noscript|//svg|//template"):
        bad.drop_tree()
    doc.make_links_absolute(base_url, resolve_base_href=True)
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in doc.iter("a"):
        href = (a.get("href") or "").strip()
        if not href.startswith(("http://", "https://")) or is_blocked_by_policy(href):
            continue
        text = re.sub(r"\s+", " ", a.text_content()).strip()
        key = normalize_url(href)
        if key not in seen:
            seen.add(key)
            links.append((text[:80], key))
    for el in doc.iter(*BLOCK_TAGS):
        el.tail = "\n" + (el.tail or "")
    raw = doc.text_content()
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return text, links


def chunks(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start : start + size])
        start += size - overlap
    return out


async def extract_companies(
    gemini: Any, page_text: str, links: list[tuple[str, str]], *, source_url: str, scout: str,
    context: str, education_only: bool = True, limit: int | None = None, stats: dict[str, int] | None = None,
) -> list[DiscoveryItem]:
    stats = stats if stats is not None else {}
    allowed_urls = {dedupe_key(u): u for _, u in links}
    out: list[DiscoveryItem] = []
    for chunk in chunks(page_text):
        if limit is not None and len(out) >= limit:
            break
        chunk_links = [(t, u) for t, u in links if t and t.casefold() in chunk.casefold()][:MAX_LINKS_PER_CHUNK]
        prompt = (
            f"SOURCE: {context} ({source_url})\n\nLINKS (anchor text -> URL):\n"
            + "\n".join(f"- {t} -> {u}" for t, u in chunk_links)
            + f"\n\nPAGE TEXT:\n{chunk}"
        )
        try:
            result = await gemini.generate_json(prompt, PageExtraction, "fast", system=PAGE_SYSTEM)
        except Exception as exc:
            log.warning("[%s] page extraction failed for %s: %s", scout, source_url, exc)
            stats["batch_errors"] = stats.get("batch_errors", 0) + 1
            continue
        for c in result.companies:
            if education_only and not c.is_education:
                stats["not_education"] = stats.get("not_education", 0) + 1
                continue
            g = check(c.evidence_span, chunk, c.company_name)
            if not g.ok:
                stats["ungrounded"] = stats.get("ungrounded", 0) + 1
                continue
            url = allowed_urls.get(dedupe_key(c.company_url)) if c.company_url else None
            founders = [f for f in dict.fromkeys(c.founders) if name_in_text(f, chunk)]
            for founder in founders or [None]:
                out.append(DiscoveryItem(
                    scout=scout, raw_company_name=c.company_name.strip(), raw_person_name=founder,
                    company_url=url, source_url=source_url, snippet=c.evidence_span[:300],
                    evidence_span=c.evidence_span, extra={"source": context},
                ))
    return out
