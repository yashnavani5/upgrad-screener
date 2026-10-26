"""Accelerators: YC companies from the public yc-oss JSON API (filtered to education), plus cohort/portfolio
pages of Indian accelerators found by search and extracted with Gemini."""

from __future__ import annotations

from typing import Any

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.scouts.pages import scout_named_pages

YC_MAX_BYTES = 60_000_000


def is_education_company(company: dict[str, Any], industries: list[str], tag_keywords: list[str]) -> bool:
    inds = {i.casefold() for i in industries}
    fields = [company.get("industry") or "", company.get("subindustry") or ""] + list(company.get("industries") or [])
    if any(f.casefold() in inds or any(f.casefold().startswith(i + " ->") for i in inds) for f in fields if f):
        return True
    # Exact tag match only: substring matching would let "Machine Learning" in through "learning".
    wanted = {k.casefold() for k in tag_keywords}
    return any(t.casefold() in wanted for t in company.get("tags") or [])


def _india_first_key(company: dict[str, Any]) -> tuple[int, int]:
    places = " ".join((company.get("regions") or []) + [company.get("all_locations") or ""]).casefold()
    return (0 if "india" in places else 1, -int(company.get("launched_at") or 0))


def filter_yc(companies: list[dict[str, Any]], industries: list[str], tag_keywords: list[str]) -> list[dict[str, Any]]:
    """Education companies, India-based first, then most recently launched."""
    picked = [c for c in companies if c.get("name") and is_education_company(c, industries, tag_keywords)]
    return sorted(picked, key=_india_first_key)


def yc_item(company: dict[str, Any], scout: str) -> DiscoveryItem:
    slug = company.get("slug")
    source = company.get("url") or (f"https://www.ycombinator.com/companies/{slug}" if slug else "https://www.ycombinator.com/companies")
    return DiscoveryItem(
        scout=scout, raw_company_name=company["name"], company_url=company.get("website") or None,
        source_url=source, snippet=company.get("one_liner") or None,
        extra={k: company.get(k) for k in ("batch", "status", "tags", "team_size", "regions", "industry", "subindustry", "all_locations")},
    )


class AcceleratorsScout(BaseScout):
    name = "accelerators"
    config_key = "accelerators"

    async def _yc(self) -> list[DiscoveryItem]:
        yc = self.conf.get("yc", {})
        try:
            data = await self.ctx.fetcher.get_json(yc["endpoint"], provider="yc-oss", max_bytes=YC_MAX_BYTES)
        except Exception as exc:
            self.note(f"yc-oss: {type(exc).__name__}: {str(exc)[:150]}")
            return []
        picked = filter_yc(data, yc.get("industries", []), yc.get("tag_keywords", []))
        self.note(f"yc-oss: {len(picked)} education companies out of {len(data)}")
        return [yc_item(c, self.name) for c in picked]

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        items: list[DiscoveryItem] = []
        if self.ctx.gemini is not None:
            items += await scout_named_pages(self, self.conf.get("portfolios", []), limit)
        else:
            self.note("portfolio pages skipped: needs Gemini")
        yc = await self._yc()
        return items + yc[: max(0, limit - len(items))]
