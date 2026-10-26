"""Hacker News (Algolia API): "Launch HN" / "Show HN" posts about education. Names parsed from titles."""

from __future__ import annotations

import re

from screener.scouts.base import BaseScout, DiscoveryItem

TITLE_RE = re.compile(
    r"^(?P<kind>Launch|Show) HN:\s*(?P<name>.+?)\s*(?:\((?P<yc>YC [A-Z]\d{2})[^)]*\))?\s*(?:[\u2013\u2014|:]|\s-\s|$)\s*(?P<desc>.*)$"
)
NOT_A_NAME = re.compile(r"^(i|we|my|our|a|an|the|this|how|why|what|open[- ]source|free)\b", re.IGNORECASE)


def parse_hn_title(title: str) -> dict[str, str | None] | None:
    """'Launch HN: Acme (YC W24) - AI tutor for X' -> {kind, name, yc, desc}; None if no product name."""
    m = TITLE_RE.match(title.strip())
    if not m:
        return None
    name = m.group("name").strip(" -")
    if not name or len(name.split()) > 4 or NOT_A_NAME.match(name):
        return None
    return {"kind": m.group("kind"), "name": name, "yc": m.group("yc"), "desc": (m.group("desc") or "").strip() or None}


class HnScout(BaseScout):
    name = "hn"
    config_key = "hn"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        out: list[DiscoveryItem] = []
        seen: set[str] = set()
        for q in self.conf.get("queries", []):
            params = {"query": q, "tags": "story", "hitsPerPage": int(self.conf.get("hits_per_query", 50))}
            try:
                data = await self.ctx.fetcher.get_json(self.conf["endpoint"], params=params, provider="hn")
            except Exception as exc:
                self.note(f"'{q}': {type(exc).__name__}: {str(exc)[:150]}")
                continue
            for hit in data.get("hits", []):
                title, oid = hit.get("title") or "", hit.get("objectID")
                if not oid or oid in seen:
                    continue
                parsed = parse_hn_title(title)
                if not parsed or not self.ctx.prefilter.matches(title, hit.get("story_text")):
                    continue
                seen.add(oid)
                url = hit.get("url")
                out.append(DiscoveryItem(
                    scout=self.name, raw_company_name=parsed["name"],  # type: ignore[arg-type]
                    company_url=url if url and "ycombinator.com" not in url else None,
                    source_url=f"https://news.ycombinator.com/item?id={oid}", snippet=title,
                    extra={"kind": parsed["kind"], "yc_batch": parsed["yc"], "points": hit.get("points"),
                           "author": hit.get("author"), "created_at": hit.get("created_at")},
                ))
        out.sort(key=lambda i: (i.extra.get("kind") != "Launch", -(i.extra.get("points") or 0)))
        return out[:limit]
