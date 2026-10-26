"""SPEC section 3 formulas as pure functions. Every constant comes from config/weights.yaml."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
from scipy.stats import rankdata


def percentile_ranks(values: Sequence[float | None]) -> list[float | None]:
    """Percentile rank within the pool: (average rank - 0.5) / n over the non-missing values, in (0, 1).
    Ties share their average rank; missing values stay None (they are not part of the pool)."""
    present = [(i, float(v)) for i, v in enumerate(values) if v is not None and not math.isnan(float(v))]
    out: list[float | None] = [None] * len(values)
    if not present:
        return out
    ranks = rankdata([v for _, v in present], method="average")
    n = len(present)
    for (i, _), r in zip(present, ranks, strict=True):
        out[i] = float((r - 0.5) / n)
    return out


def log_money(amount: float | None) -> float | None:
    """log1p of a non-negative money amount (money is log-transformed before ranking)."""
    if amount is None or amount < 0:
        return None
    return math.log1p(amount)


def recency_weight(age_days: float | None, half_life_days: float) -> float:
    """0.5 ** (age / half_life); unknown age gets no decay credit (weight 0)."""
    if age_days is None:
        return 0.0
    return 0.5 ** (max(0.0, age_days) / half_life_days)


def track_record_founder(f: dict[str, float], weights: dict[str, float]) -> float:
    """T_founder = sum(weight * feature) over SPEC's seven components (features in [0, 1], missing = 0)."""
    return sum(float(weights[k]) * float(f.get(k) or 0.0) for k in weights)


def track_record(founders: list[dict[str, float]], weights: dict[str, float], team_depth: bool,
                 bonus: float, cap: float) -> float:
    """T = min(cap, max over co-founders + bonus if team depth)."""
    if not founders:
        return 0.0
    best = max(track_record_founder(f, weights) for f in founders)
    return min(cap, best + (bonus if team_depth else 0.0))


def momentum(weighted: list[tuple[float, float]]) -> float | None:
    """M = mean over available momentum features of pct * recency_weight. None if no feature is available."""
    if not weighted:
        return None
    return float(np.mean([p * w for p, w in weighted]))


def interesting(m: float | None, n: float | None, w_m: float, w_n: float) -> float:
    """I = w_m * M + w_n * N (missing parts count as 0)."""
    return w_m * (m or 0.0) + w_n * (n or 0.0)


def shrink(raw: float, n_signals: int, pool_mean: float, k: float) -> float:
    """s_adj = (n * s_raw + k * mu_pool) / (n + k)."""
    return (n_signals * raw + k * pool_mean) / (n_signals + k)


def confidence(verified: int, expected: float, cap: float = 1.0) -> float:
    """C = min(cap, verified / expected)."""
    if expected <= 0:
        return 0.0
    return min(cap, verified / expected)


def final_score(t: float, i: float, f: float, c: float, exponents: dict[str, float], epsilon: float) -> float:
    """Final = T^a * I^b * F^c * C^d with each factor floored at epsilon before exponentiation."""
    vals = {"T": t, "I": i, "F": f, "C": c}
    out = 1.0
    for k, e in exponents.items():
        out *= max(float(epsilon), float(vals[k])) ** float(e)
    return out


def blend(final: float, bt_pct: float | None, w_final: float, w_bt: float) -> float:
    """Final' = w_final * Final + w_bt * pct(BT); items without a BT score get no BT credit."""
    return w_final * final + w_bt * (bt_pct or 0.0)


def cosine(u: Sequence[float] | None, v: Sequence[float] | None) -> float:
    if u is None or v is None:
        return 0.0
    a, b = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / d) if d else 0.0


def kendall_tau(baseline: list[Any], scores: dict[Any, float]) -> float:
    """Kendall tau between the baseline order of `baseline` items and their order under `scores`."""
    from scipy.stats import kendalltau

    if len(baseline) < 2:
        return 1.0
    tau, _ = kendalltau(list(range(len(baseline))), [-scores[x] for x in baseline])
    return float(tau) if tau == tau else 1.0  # nan (all ties) -> 1.0
