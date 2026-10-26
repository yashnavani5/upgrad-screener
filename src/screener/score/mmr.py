"""Maximal Marginal Relevance shortlist selection with per-fit-type and per-region minimums."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from screener.score.core import cosine


@dataclass(frozen=True)
class MmrItem:
    key: Any
    score: float              # Final'
    fit_type: str
    embedding: list[float] | None
    region: str = "unknown"   # india | outside | unknown


def similarity(a: MmrItem, b: MmrItem, same_type_bonus: float) -> float:
    return cosine(a.embedding, b.embedding) + (same_type_bonus if a.fit_type == b.fit_type else 0.0)


def select(items: list[MmrItem], k: int, lam: float, same_type_bonus: float, min_per_type: int,
           fit_types: list[str], min_region: dict[str, int] | None = None, pinned: set[Any] | None = None) -> list[MmrItem]:
    """Greedy MMR: argmax lam*score - (1-lam)*max_sim(x, selected). Minimums (capped by what the pool has):
    `min_per_type` per fit type and `min_region` per region. When the remaining slots are only just enough to
    meet a minimum, only items that reduce that deficit are eligible (items reducing both are preferred).
    `pinned` keys (the policy's must-consider reviewer picks) are taken first, best score first, up to k; they
    count toward the minimums and the rest is chosen by MMR as usual."""
    pool = sorted(items, key=lambda x: (-x.score, str(x.key)))
    avail_t = Counter(x.fit_type for x in pool)
    avail_r = Counter(x.region for x in pool)
    need_t = {t: min(min_per_type, avail_t.get(t, 0)) for t in fit_types}
    need_r = {r: min(n, avail_r.get(r, 0)) for r, n in (min_region or {}).items()}
    selected: list[MmrItem] = [x for x in pool if pinned and x.key in pinned][:k]
    pool = [x for x in pool if x not in selected]
    while pool and len(selected) < k:
        have_t = Counter(x.fit_type for x in selected)
        have_r = Counter(x.region for x in selected)
        def_t = {t for t in fit_types if have_t.get(t, 0) < need_t[t]}
        def_r = {r for r in need_r if have_r.get(r, 0) < need_r[r]}
        slots = k - len(selected)
        bind_t = bool(def_t) and sum(need_t[t] - have_t.get(t, 0) for t in def_t) >= slots
        bind_r = bool(def_r) and sum(need_r[r] - have_r.get(r, 0) for r in def_r) >= slots
        candidates = pool
        if bind_t or bind_r:
            both = [x for x in pool if (not bind_t or x.fit_type in def_t) and (not bind_r or x.region in def_r)]
            either = [x for x in pool if (bind_t and x.fit_type in def_t) or (bind_r and x.region in def_r)]
            candidates = both or either or pool

        def mmr(x: MmrItem) -> tuple[float, float, str]:
            penalty = max((similarity(x, s, same_type_bonus) for s in selected), default=0.0)
            return (lam * x.score - (1 - lam) * penalty, x.score, str(x.key))

        best = max(candidates, key=mmr)
        selected.append(best)
        pool.remove(best)
    return selected
