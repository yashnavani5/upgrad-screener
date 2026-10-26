"""Blocking, pair features, merge rules and domain-aware union-find for company resolution."""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from rapidfuzz.distance import JaroWinkler

Decision = Literal["merge", "separate", "ambiguous"]


@dataclass
class Record:
    """One or more discoveries that share scout, source and normalized name."""

    key: str
    name: str                      # most common raw name in the group
    norm: str
    domain: str | None = None
    website: str | None = None
    founders: set[str] = field(default_factory=set)   # normalized founder names
    surnames: set[str] = field(default_factory=set)
    city: str | None = None
    description: str = ""
    discovery_ids: list[Any] = field(default_factory=list)
    raw_names: list[str] = field(default_factory=list)
    scouts: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    domain_from_name: bool = False


def record_key(scout: str, source_url: str, norm: str) -> str:
    return hashlib.sha256(f"{scout}\x00{source_url}\x00{norm}".encode()).hexdigest()


def pair_key(a: Record, b: Record) -> str:
    x, y = sorted((a.key, b.key))
    return hashlib.sha256(f"{x}|{y}".encode()).hexdigest()


def blocking_keys(r: Record, prefix_chars: int) -> set[str]:
    keys = set()
    if r.domain:
        keys.add(f"d:{r.domain}")
    if r.norm:
        keys.add(f"n:{r.norm.replace(' ', '')[:prefix_chars]}")
    keys |= {f"s:{s}" for s in r.surnames}
    return keys


def candidate_pairs(records: list[Record], prefix_chars: int) -> list[tuple[int, int]]:
    """Index pairs that share at least one blocking key (compare only within blocks)."""
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        for k in blocking_keys(r, prefix_chars):
            blocks[k].append(i)
    pairs: set[tuple[int, int]] = set()
    for members in blocks.values():
        for i, j in itertools.combinations(sorted(set(members)), 2):
            pairs.add((i, j))
    return sorted(pairs)


def jaro_winkler(a: str, b: str) -> float:
    return float(JaroWinkler.normalized_similarity(a, b)) if a and b else 0.0


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def cosine(u: list[float] | None, v: list[float] | None) -> float | None:
    if u is None or v is None:
        return None
    a, b = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denom) if denom else None


@dataclass(frozen=True)
class PairFeatures:
    jw: float
    same_domain: bool
    domain_conflict: bool
    founder_jaccard: float
    cosine: float | None
    city_match: bool | None

    def as_dict(self) -> dict[str, Any]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def features(a: Record, b: Record, emb_a: list[float] | None = None, emb_b: list[float] | None = None) -> PairFeatures:
    both = bool(a.domain and b.domain)
    return PairFeatures(
        jw=jaro_winkler(a.norm, b.norm),
        same_domain=both and a.domain == b.domain,
        domain_conflict=both and a.domain != b.domain,
        founder_jaccard=jaccard(a.founders, b.founders),
        cosine=cosine(emb_a, emb_b),
        city_match=(a.city == b.city) if a.city and b.city else None,
    )


def decide(f: PairFeatures, thresholds: dict[str, Any]) -> tuple[Decision, str]:
    """Rules: same domain -> merge; different real domains -> never; JW >= merge with founder overlap or
    similar description -> merge; ambiguous band -> tiebreak."""
    if f.same_domain:
        return "merge", "domain"
    if f.domain_conflict:
        return "separate", "domain_conflict"
    jw_merge, jw_amb = float(thresholds["jw_merge"]), float(thresholds["jw_ambiguous"])
    cos_merge = float(thresholds["cosine_merge"])
    if f.jw >= jw_merge:
        if f.founder_jaccard > 0:
            return "merge", "rule:name+founder"
        if f.cosine is not None and f.cosine >= cos_merge:
            return "merge", "rule:name+description"
        if thresholds.get("tiebreak_unsupported_high_jw", True):
            return "ambiguous", "high_jw_unsupported"
        return "separate", "high_jw_unsupported"
    if f.jw >= jw_amb:
        return "ambiguous", "jw_band"
    return "separate", "low_similarity"


class DomainUnionFind:
    """Union-find that refuses to join components with different known domains."""

    def __init__(self, records: list[Record]) -> None:
        self.parent = list(range(len(records)))
        self.domain: dict[int, str | None] = {i: r.domain for i, r in enumerate(records)}

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> bool:
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return True
        di, dj = self.domain[ri], self.domain[rj]
        if di and dj and di != dj:
            return False
        self.parent[rj] = ri
        self.domain[ri] = di or dj
        return True

    def set_domain(self, i: int, domain: str) -> None:
        self.domain[self.find(i)] = domain

    def groups(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            out[self.find(i)].append(i)
        return dict(out)


def canonical_name(records: list[Record], domain: str | None, label_of: Any) -> str:
    """Most frequent normalized name (bonus if it equals the domain label), shown in its most common casing."""
    norm_counts: Counter[str] = Counter()
    raw_by_norm: dict[str, Counter[str]] = defaultdict(Counter)
    for r in records:
        for raw in r.raw_names:
            norm_counts[r.norm] += 1
            raw_by_norm[r.norm][raw] += 1
    label = (label_of(domain) or "").replace("-", "") if domain else ""

    def score(norm: str) -> tuple[float, int, str]:
        bonus = 2 if label and norm.replace(" ", "") == label else 0
        return (norm_counts[norm] + bonus, -len(norm), norm)

    best_norm = max(norm_counts, key=score)
    candidates = raw_by_norm[best_norm]
    # Prefer a casing that isn't a bare domain and isn't all lowercase when alternatives exist.
    return max(candidates, key=lambda raw: (candidates[raw], "." not in raw, not raw.islower(), raw))
