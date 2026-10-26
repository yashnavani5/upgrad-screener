"""Fit categories against the upGrad gap map. Each category (policy.py; default Acquire / Partner / Co-build) is
a generic lens:

    base  = capability_weight x cap + segment_weight x seg + both_weight x seg x cap
    score = base x geo (if use_geo) x size factor x stage factor (+ founder boost), capped at 1

cap / seg = overlap with wanted capabilities / business units (2 matching tags = full); size factor = 0 outside the
category's funding range, 1 when size is verified, `unknown_size_factor` when it is not; stage = very early / early /
later. The defaults reproduce the original three lenses exactly. F = max over enabled categories; fit_type = argmax.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LENSES = ("acquire", "partner", "co_build")  # the default category ids


@dataclass
class FitInput:
    segments: set[str] = field(default_factory=set)       # upGrad business-unit ids (supported claims)
    capabilities: set[str] = field(default_factory=set)   # wanted-capability ids (supported claims)
    geo: str = "unknown"                                   # india | outside | unknown
    total_raised_usd: float | None = None
    stages: list[str] = field(default_factory=list)
    founder_t: float = 0.0
    size_known: bool | None = None                         # None: known iff total_raised_usd is known
    age_years: int | None = None                           # from a supported founded_year claim


def very_early(fi: FitInput, cb: dict[str, Any]) -> bool:
    """Pre-seed/seed-stage: too early to acquire, right for co-building. Raised amount beats stage beats age."""
    stages = {s.lower() for s in fi.stages}
    seedish = {s.lower() for s in cb["very_early_stages"]}
    if fi.total_raised_usd is not None:
        return fi.total_raised_usd < float(cb["very_early_max_raised_usd"]) and (not stages or stages <= seedish)
    if stages:
        return stages <= seedish
    return fi.age_years is not None and fi.age_years <= int(cb["very_early_max_age_years"])


def stage_of(fi: FitInput, cb: dict[str, Any]) -> str:
    """very_early | early | later (thresholds in weights.yaml fit.co_build)."""
    if very_early(fi, cb):
        return "very_early"
    stages = {s.lower() for s in fi.stages}
    raised = fi.total_raised_usd
    early = (raised is None or raised < float(cb["early_max_raised_usd"])) and (
        not stages or stages <= {s.lower() for s in cb["early_stages"]})
    return "early" if early else "later"


def default_categories(conf: dict[str, Any]) -> list[dict[str, Any]]:
    """The original three lenses expressed as generic categories (used when no policy defines categories)."""
    aq, cb = conf["acquire"], conf["co_build"]
    base = {"require_capabilities": [], "require_segments": [], "min_raised_usd": None, "use_geo": True,
            "founder_boost_min_t": None, "founder_boost": 0.0, "founder_boost_early_only": True}
    return [
        {"id": "acquire", "label": "Acquire", "enabled": True, "rule": {**base, "capability_weight": 1.0, "segment_weight": 0.0,
         "both_weight": 0.0, "max_raised_usd": aq["max_total_raised_usd"], "unknown_size_factor": aq["unknown_size_factor"],
         "stage": {"very_early": aq["very_early_factor"], "early": 1.0, "later": 1.0}}},
        {"id": "partner", "label": "Partner", "enabled": True, "rule": {**base, "capability_weight": 0.0, "segment_weight": 0.5,
         "both_weight": 0.5, "max_raised_usd": None, "unknown_size_factor": 1.0,
         "stage": {"very_early": 1.0, "early": 1.0, "later": 1.0}}},
        {"id": "co_build", "label": "Co-build", "enabled": True, "rule": {**base, "capability_weight": 1.0, "segment_weight": 0.0,
         "both_weight": 0.0, "max_raised_usd": None, "unknown_size_factor": 1.0,
         "stage": {"very_early": 1.0, "early": cb["early_factor"], "later": 0.5},
         "founder_boost_min_t": cb["founder_t_boost_min"], "founder_boost": cb["boost"]}},
    ]


def categories(conf: dict[str, Any]) -> list[dict[str, Any]]:
    """Enabled categories in priority order (ties go to the earlier one)."""
    cats = conf.get("categories") or default_categories(conf)
    return [c for c in cats if c.get("enabled", True)]


def lens_scores(fi: FitInput, gap_map: dict[str, Any], conf: dict[str, Any]) -> dict[str, float]:
    wanted = {c["id"] for c in gap_map.get("wanted_capabilities", [])}
    units = {b["id"] for b in gap_map.get("business_units", [])}
    target = float(conf.get("tag_target", 2))
    caps, segs = fi.capabilities & wanted, fi.segments & units
    cap, seg = min(1.0, len(caps) / target), min(1.0, len(segs) / target)
    geo = float(conf["geo"].get(fi.geo, conf["geo"]["unknown"]))
    raised = fi.total_raised_usd
    known = fi.size_known if fi.size_known is not None else raised is not None
    stage = stage_of(fi, conf["co_build"])
    out: dict[str, float] = {}
    for c in categories(conf):
        r = c["rule"]
        if (r.get("require_capabilities") and not caps & set(r["require_capabilities"])) or (
                r.get("require_segments") and not segs & set(r["require_segments"])):
            out[c["id"]] = 0.0
            continue
        base = float(r["capability_weight"]) * cap + float(r["segment_weight"]) * seg + float(r["both_weight"]) * seg * cap
        if (r.get("max_raised_usd") is not None and raised is not None and raised > float(r["max_raised_usd"])) or (
                r.get("min_raised_usd") is not None and raised is not None and raised < float(r["min_raised_usd"])):
            size = 0.0
        else:
            size = 1.0 if known else float(r.get("unknown_size_factor", 1.0))
        score = base * (geo if r.get("use_geo", True) else 1.0) * size * float(r["stage"][stage])
        boost_min = r.get("founder_boost_min_t")
        if boost_min is not None and fi.founder_t >= float(boost_min) and (stage != "later" or not r.get("founder_boost_early_only", True)):
            score = min(1.0, score + float(r.get("founder_boost", 0.0)))
        out[c["id"]] = min(1.0, score)
    return out


def best_lens(scores: dict[str, float]) -> tuple[str, float]:
    order = list(scores)  # insertion order = category priority
    lens = max(order, key=lambda k: (scores[k], -order.index(k)))
    return lens, scores[lens]
