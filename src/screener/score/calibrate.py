"""`screener calibrate`: recall@50 on seed companies and Kendall-tau stability of the top 15 under +/-20%
random weight perturbations (deterministic part of the score; the pairwise judge is not re-run)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.models import Candidate, Company, Score
from screener.resolve.normalize import normalize_name
from screener.score import core
from screener.score.features import CompanyData
from screener.score.service import compute, perturbed


@dataclass
class StabilityReport:
    trials: int
    top_k: int
    taus: list[float] = field(default_factory=list)
    overlaps: list[float] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, float]:
        if not self.taus:
            return {}
        return {"tau_mean": float(np.mean(self.taus)), "tau_min": float(np.min(self.taus)),
                "tau_max": float(np.max(self.taus)), "topk_overlap_mean": float(np.mean(self.overlaps))}


def stability(datas: list[CompanyData], weights: dict[str, Any], gap_map: dict[str, Any],
              embeddings: dict[Any, list[float]], today: date, eligible: set[Any], conf: dict[str, Any]) -> StabilityReport:
    k = int(conf["stability_top_k"])
    base = {s.key: s.final for s in compute(datas, weights, gap_map, embeddings, today) if s.key in eligible}
    top = sorted(base, key=lambda x: -base[x])[:k]
    rng = np.random.default_rng(int(conf.get("random_seed", 7)))
    report = StabilityReport(int(conf.get("perturbation_trials", 20)), k)
    for _ in range(report.trials):
        w = perturbed(weights, float(conf["perturbation_pct"]), rng)
        scores = {s.key: s.final for s in compute(datas, w, gap_map, embeddings, today) if s.key in eligible}
        report.taus.append(core.kendall_tau(top, scores))
        new_top = set(sorted(scores, key=lambda x: -scores[x])[:k])
        report.overlaps.append(len(new_top & set(top)) / max(1, len(top)))
    return report


def _names(entries: list[Any] | None) -> list[str]:
    return [e.get("company") or e.get("name") for e in entries or [] if isinstance(e, dict)] + [
        e for e in entries or [] if isinstance(e, str)]


def recall_at(session: Session, seeds: dict[str, Any], at: int) -> tuple[float | None, list[str], list[str], list[str]]:
    """Share of seed positives ranked within the top `at` (by Final') of the latest scored run, plus the seed
    negatives that land in the top `at` (SPEC 3.11). Returns (recall or None if no positives, found, missing,
    negatives_in_top)."""
    from screener.score.seeds import scored_run_id

    positives, negatives = _names(seeds.get("positives")), _names(seeds.get("negatives"))
    run_id = scored_run_id(session)
    rows = session.execute(select(Company.name, Score.rank).join(Candidate, Candidate.company_id == Company.id)
                           .join(Score, Score.candidate_id == Candidate.id).where(Score.run_id == run_id)).all() if run_id else []
    ranks = {normalize_name(n): r for n, r in rows}

    def top(name: str) -> bool:
        r = ranks.get(normalize_name(name))
        return r is not None and r <= at

    neg_top = [n for n in negatives if top(n)]
    if not positives:
        return None, [], [], neg_top
    found = [p for p in positives if top(p)]
    return len(found) / len(positives), found, [p for p in positives if p not in found], neg_top
