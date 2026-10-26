"""Cheap education keyword prefilter, applied before any LLM call. Word-boundary, case-insensitive."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


@lru_cache(maxsize=8)
def _compile(keywords: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first so "test prep" wins over shorter overlaps. Spaces and hyphens inside a keyword match
    # a space, a hyphen or nothing: "test prep" ~ "test-prep" ~ "testprep", "k-12" ~ "k12".
    parts = []
    for kw in sorted({k.strip().lower() for k in keywords if k.strip()}, key=len, reverse=True):
        tokens = [t for t in re.split(r"[\s\-]+", kw) if t]
        parts.append(r"[\s\-]*".join(re.escape(t) for t in tokens))
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])", re.IGNORECASE)


class Prefilter:
    def __init__(self, keywords: Iterable[str]) -> None:
        self.keywords = tuple(keywords)
        if not self.keywords:
            raise ValueError("prefilter needs at least one keyword (config/sources.yaml prefilter_keywords)")
        self._pattern = _compile(self.keywords)

    def matches(self, *texts: str | None) -> bool:
        return any(self._pattern.search(t) for t in texts if t)

    def matched(self, *texts: str | None) -> list[str]:
        found: list[str] = []
        for t in texts:
            if t:
                found += [m.group(0).lower() for m in self._pattern.finditer(t)]
        return sorted(set(found))
