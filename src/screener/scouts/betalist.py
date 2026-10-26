"""BetaList education pages (free stand-in for Product Hunt). Parsed from HTML; sponsored rows skipped."""

from __future__ import annotations

import re

import lxml.html

from screener.scouts.base import BaseScout, DiscoveryItem

BASE = "https://betalist.com"


def parse_betalist(html: str) -> list[dict[str, str]]:
    """[{name, tagline, path}] from startup rows; rows marked BOOSTED (paid placement) are skipped."""
    doc = lxml.html.fromstring(html)
    rows = []
    for row in doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " startup-row ")]'):
        texts = [re.sub(r"\s+", " ", t).strip() for t in row.itertext()]
        texts = [t for t in texts if t]
        if any(t.upper() == "BOOSTED" for t in texts):
            continue
        hrefs = row.xpath('.//a[starts-with(@href, "/startups/")]/@href')
        if not hrefs or len(texts) < 1:
            continue
        rows.append({"name": texts[0], "tagline": texts[1] if len(texts) > 1 else "", "path": hrefs[0]})
    return rows


class BetalistScout(BaseScout):
    name = "betalist"
    config_key = "betalist"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        out: list[DiscoveryItem] = []
        for page in self.conf.get("pages", []):
            if len(out) >= limit:
                break
            result = await self.ctx.fetcher.fetch(page, allow_fallbacks=False)
            if not result.ok or not result.html:
                self.note(f"{page}: {result.outcome} ({result.error})")
                continue
            for row in parse_betalist(result.html):
                out.append(DiscoveryItem(
                    scout=self.name, raw_company_name=row["name"], source_url=BASE + row["path"],
                    snippet=row["tagline"] or None, extra={"listing": page},
                ))
        return out
