"""Preliminary priority for ordering enrichment (and PDL's top-100 budget). Cheap signals only; this is NOT
the final score (SPEC section 3 is computed later, in code, from verified claims)."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

INDIA_WORDS = ("india", "bengaluru", "bangalore", "mumbai", "delhi", "gurugram", "gurgaon", "hyderabad", "pune",
               "chennai", "noida", "kolkata", "ahmedabad", "jaipur", "kochi")


@dataclass(frozen=True)
class CompanySignals:
    scouts: int
    mentions: int
    has_domain: bool
    india: bool
    has_founder: bool


def signals_from_discoveries(rows: list[Any], has_domain: bool, has_founder: bool) -> CompanySignals:
    text = " ".join(
        f"{r.snippet or ''} {r.evidence_span or ''} {(r.extra or {}).get('all_locations') or ''} "
        f"{' '.join((r.extra or {}).get('regions') or [])} {r.source_url}"
        for r in rows
    ).lower()
    india = any(w in text for w in INDIA_WORDS) or any(r.source_url and ".in/" in r.source_url for r in rows)
    return CompanySignals(len({r.scout for r in rows}), len(rows), has_domain, india, has_founder)


def priority(s: CompanySignals, weights: dict[str, float]) -> float:
    return round(
        weights.get("per_scout", 1.0) * s.scouts
        + weights.get("per_mention_log", 1.0) * math.log1p(s.mentions)
        + weights.get("has_domain", 1.0) * s.has_domain
        + weights.get("india_signal", 1.5) * s.india
        + weights.get("has_founder", 0.5) * s.has_founder,
        4,
    )


def rank_companies(session: Any, weights: dict[str, float]) -> list[tuple[Any, float]]:
    """[(Company, priority)] highest first."""
    from sqlalchemy import select

    from screener.models import Company, CompanyFounder, Discovery

    by_company: dict[Any, list[Any]] = defaultdict(list)
    for d in session.scalars(select(Discovery).where(Discovery.company_id.is_not(None))).all():
        by_company[d.company_id].append(d)
    with_founder = set(session.scalars(select(CompanyFounder.company_id)).all())
    ranked = []
    for c in session.scalars(select(Company)).all():
        s = signals_from_discoveries(by_company.get(c.id, []), bool(c.domain), c.id in with_founder)
        ranked.append((c, priority(s, weights)))
    return sorted(ranked, key=lambda x: (-x[1], x[0].name))
