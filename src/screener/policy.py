"""AI Policy: every editable screening rule in one versioned document (dashboard: /policy).

The policy covers fit categories (add, rename, define), filters (gates), scoring weights, shortlist selection and the
lists the rules use (competitors, upGrad brands, business units, wanted capabilities). The default policy is built
from config/*.yaml, so with no saved version the screener behaves exactly as configured. A saved version overlays the
YAML at score time (`apply`). Versions live in `policy_versions`; the active one is used by every score.

Code still computes every score; the policy only sets the numbers and switches the code uses.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

DEFAULT_IDS = ("acquire", "partner", "co_build")


class StageFactors(BaseModel):
    very_early: float = Field(1.0, ge=0, le=1, description="pre-seed / seed, < $3M raised or <= 3 years old")
    early: float = Field(1.0, ge=0, le=1, description="< $10M raised, up to pre-Series A")
    later: float = Field(1.0, ge=0, le=1, description="everything else")


class CategoryRule(BaseModel):
    capability_weight: float = Field(1.0, ge=0, le=1, description="weight of matching a wanted capability")
    segment_weight: float = Field(0.0, ge=0, le=1, description="weight of matching an upGrad business unit")
    both_weight: float = Field(0.0, ge=0, le=1, description="weight of matching both (segment x capability)")
    require_capabilities: list[str] = Field(default_factory=list, description="at least one of these, else 0")
    require_segments: list[str] = Field(default_factory=list, description="at least one of these, else 0")
    min_raised_usd: float | None = Field(None, ge=0)
    max_raised_usd: float | None = Field(None, ge=0, description="above this the category scores 0")
    unknown_size_factor: float = Field(1.0, ge=0, le=1, description="multiplier when funding/headcount is unverified")
    stage: StageFactors = Field(default_factory=StageFactors)
    founder_boost_min_t: float | None = Field(None, ge=0, le=1, description="founder T at or above this adds the boost")
    founder_boost: float = Field(0.0, ge=0, le=1)
    founder_boost_early_only: bool = True
    use_geo: bool = True


class Category(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,19}$")
    label: str = Field(min_length=1, max_length=40)
    description: str = Field("", max_length=400)
    enabled: bool = True
    rule: CategoryRule = Field(default_factory=CategoryRule)


class Filters(BaseModel):
    require_education: bool = True
    exclude_inactive: bool = True
    exclude_acquired: bool = True
    exclude_competitors: bool = True
    exclude_upgrad_owned: bool = True
    max_total_raised_usd: float | None = Field(100_000_000, ge=0)
    max_headcount: int | None = Field(1000, ge=0)
    require_identity_verified: bool = True
    require_current_role: bool = True
    require_company_existence: bool = True
    min_track_record_claims: int = Field(3, ge=0, le=20)
    regions_allowed: dict[str, bool] = Field(default_factory=lambda: {"india": True, "outside": True, "unknown": True})
    founded_after: int | None = Field(None, ge=1900, le=2100, description="exclude companies founded before this year")
    founded_before: int | None = Field(None, ge=1900, le=2100, description="exclude companies founded after this year")
    min_total_raised_usd: float | None = Field(None, ge=0, description="exclude companies with less verified funding")
    require_verified_funding: bool = False
    excluded_stages: list[str] = Field(default_factory=list, description="e.g. 'series c', 'series d'")
    min_confidence: float = Field(0.0, ge=0, le=1)
    min_final_score: float = Field(0.0, ge=0, le=1)
    min_verified_claims: int = Field(0, ge=0)

    @field_validator("excluded_stages")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return sorted({s.strip().lower() for s in v if s.strip()})


class Weights(BaseModel):
    track_record: dict[str, float]
    team_depth_bonus: float = Field(ge=0, le=1)
    momentum: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    momentum_half_life_days: float = Field(gt=0)
    final_exponents: dict[str, float]
    region_multiplier: dict[str, float]
    judge_blend: float = Field(ge=0, le=1, description="share of the pairwise judge in Final' (rest = Final)")
    shrinkage_k: float = Field(ge=0)


class Selection(BaseModel):
    shortlist_size: int = Field(15, ge=1, le=50)
    min_per_category: int = Field(2, ge=0, le=10)
    min_india: int = Field(10, ge=0, le=50)
    diversity_lambda: float = Field(0.75, ge=0, le=1, description="1 = pure score, lower = more variety")
    judge_top_n: int = Field(60, ge=0, le=200)


class Item(BaseModel):
    id: str
    name: str


class Lists(BaseModel):
    competitors: list[str] = Field(default_factory=list)
    upgrad_owned: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list, description="companies never shortlisted")
    must_consider: list[str] = Field(default_factory=list,
                                     description="reviewer picks: guaranteed a shortlist slot when every gate and check passes")
    business_units: list[Item] = Field(default_factory=list)
    wanted_capabilities: list[Item] = Field(default_factory=list)


class Policy(BaseModel):
    categories: list[Category]
    filters: Filters
    weights: Weights
    selection: Selection
    lists: Lists

    @field_validator("categories")
    @classmethod
    def _categories(cls, v: list[Category]) -> list[Category]:
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("category ids must be unique")
        if not any(c.enabled for c in v):
            raise ValueError("at least one category must be enabled")
        return v


# --- defaults from config/*.yaml ----------------------------------------------------------------------


def default_policy(cfg: Any) -> Policy:
    from screener.score.fit import default_categories

    w, ex, gap = cfg.weights, cfg.exclusions, cfg.gap_map
    descriptions = {
        "acquire": "Fills a capability upGrad wants and is below the size ceiling; very early companies are too early to buy.",
        "partner": "Complements an existing upGrad business unit; extra value if it also brings a wanted capability.",
        "co_build": "Very early company in a wanted capability, best with a strong founder track record.",
    }
    cats = [Category(id=c["id"], label=c["label"], description=descriptions.get(c["id"], ""), rule=CategoryRule(**c["rule"]))
            for c in default_categories(w["fit"])]
    size = ex.get("size_ceiling") or {}
    return Policy(
        categories=cats,
        filters=Filters(max_total_raised_usd=size.get("max_total_raised_usd"), max_headcount=size.get("max_headcount"),
                        min_track_record_claims=int(w["shortlist_gate"]["min_verified_signal_claims"])),
        weights=Weights(
            track_record=dict(w["track_record"]["weights"]), team_depth_bonus=w["track_record"]["team_depth_bonus"],
            momentum=w["interesting"]["weights"]["momentum"], novelty=w["interesting"]["weights"]["novelty"],
            momentum_half_life_days=w["interesting"]["momentum"]["recency_half_life_days"],
            final_exponents=dict(w["final"]["exponents"]), region_multiplier=dict(w["final"].get("region_multiplier") or {}),
            judge_blend=w["pairwise"]["blend"]["bt_pct"], shrinkage_k=w["shrinkage"]["k"]),
        selection=Selection(shortlist_size=w["mmr"]["shortlist_max"], min_per_category=w["mmr"]["min_per_fit_type"],
                            min_india=w["mmr"].get("min_india", 0), diversity_lambda=w["mmr"]["lambda"], judge_top_n=w["pairwise"]["top_n"]),
        lists=Lists(competitors=list(ex.get("competitors_at_scale") or []), upgrad_owned=list(ex.get("upgrad_owned") or []),
                    business_units=[Item(id=b["id"], name=b["name"]) for b in gap.get("business_units", [])],
                    wanted_capabilities=[Item(id=c["id"], name=c["name"]) for c in gap.get("wanted_capabilities", [])]),
    )


# --- overlay ---------------------------------------------------------------------------------------------


def apply(cfg: Any, policy: Policy, version: int | None = None) -> Any:
    """A copy of cfg whose weights / exclusions / gap_map follow the policy (YAML stays the default)."""
    w = copy.deepcopy(cfg.weights)
    p = policy
    w["track_record"]["weights"] = dict(p.weights.track_record)
    w["track_record"]["team_depth_bonus"] = p.weights.team_depth_bonus
    w["interesting"]["weights"] = {"momentum": p.weights.momentum, "novelty": p.weights.novelty}
    w["interesting"]["momentum"]["recency_half_life_days"] = p.weights.momentum_half_life_days
    w["final"]["exponents"] = dict(p.weights.final_exponents)
    w["final"]["region_multiplier"] = dict(p.weights.region_multiplier)
    w["pairwise"]["blend"] = {"final": round(1 - p.weights.judge_blend, 4), "bt_pct": p.weights.judge_blend}
    w["pairwise"]["top_n"] = p.selection.judge_top_n
    w["shrinkage"]["k"] = p.weights.shrinkage_k
    w["mmr"].update(shortlist_max=p.selection.shortlist_size, min_per_fit_type=p.selection.min_per_category,
                    min_india=p.selection.min_india, **{"lambda": p.selection.diversity_lambda})
    w["shortlist_gate"]["min_verified_signal_claims"] = p.filters.min_track_record_claims
    w["fit"]["categories"] = [c.model_dump() for c in p.categories if c.enabled]
    w["policy"] = {"version": version, "filters": p.filters.model_dump(), "blocklist": list(p.lists.blocklist),
                   "must_consider": list(p.lists.must_consider)}
    ex = copy.deepcopy(cfg.exclusions)
    ex["competitors_at_scale"] = list(p.lists.competitors)
    ex["upgrad_owned"] = list(p.lists.upgrad_owned)
    ex["size_ceiling"] = {"max_total_raised_usd": p.filters.max_total_raised_usd or float("inf"),
                          "max_headcount": p.filters.max_headcount if p.filters.max_headcount is not None else 10**9}
    gap = copy.deepcopy(cfg.gap_map)
    gap["business_units"] = [i.model_dump() for i in p.lists.business_units]
    gap["wanted_capabilities"] = [i.model_dump() for i in p.lists.wanted_capabilities]
    return replace(cfg, weights=w, exclusions=ex, gap_map=gap)


def category_labels(cfg: Any) -> dict[str, str]:
    cats = cfg.weights.get("fit", {}).get("categories")
    if cats:
        return {c["id"]: c["label"] for c in cats}
    return {"acquire": "Acquire", "partner": "Partner", "co_build": "Co-build"}


# --- versions --------------------------------------------------------------------------------------------


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def active(session: Session, cfg: Any) -> tuple[Policy, int | None]:
    from screener.models import PolicyVersion

    row = session.scalar(select(PolicyVersion).where(PolicyVersion.status == "active").order_by(PolicyVersion.version.desc()))
    return (Policy.model_validate(row.policy), row.version) if row else (default_policy(cfg), None)


def effective_config(session: Session, cfg: Any) -> Any:
    """The config every score uses: YAML overlaid by the active policy version (if any)."""
    policy, version = active(session, cfg)
    return apply(cfg, policy, version)


def diff(old: Policy, new: Policy) -> list[dict[str, Any]]:
    """Flat list of changed settings: [{path, before, after}]."""
    out: list[dict[str, Any]] = []

    def walk(a: Any, b: Any, path: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        elif isinstance(a, list) and isinstance(b, list) and all(isinstance(x, dict) and "id" in x for x in a + b):
            ia, ib = {x["id"]: x for x in a}, {x["id"]: x for x in b}
            for k in sorted(set(ia) | set(ib)):
                walk(ia.get(k), ib.get(k), f"{path}[{k}]")
        elif a != b:
            out.append({"path": path, "before": a, "after": b})

    walk(old.model_dump(), new.model_dump(), "")
    return out


def save(session: Session, cfg: Any, policy: Policy, *, author: str, note: str, activate: bool) -> int:
    from sqlalchemy import func, update

    from screener.models import PolicyVersion

    current, _ = active(session, cfg)
    version = (session.scalar(select(func.max(PolicyVersion.version))) or 0) + 1
    if activate:
        session.execute(update(PolicyVersion).where(PolicyVersion.status == "active").values(status="archived"))
    session.add(PolicyVersion(id=uuid.uuid4(), version=version, status="active" if activate else "draft",
                              author=author[:120], note=note[:500], policy=policy.model_dump(),
                              changes=diff(current, policy), created_at=utcnow()))
    session.commit()
    return version


def activate(session: Session, version: int) -> None:
    from sqlalchemy import update

    from screener.models import PolicyVersion

    if session.scalar(select(PolicyVersion.id).where(PolicyVersion.version == version)) is None:
        raise ValueError(f"policy version {version} does not exist")
    session.execute(update(PolicyVersion).where(PolicyVersion.status == "active").values(status="archived"))
    session.execute(update(PolicyVersion).where(PolicyVersion.version == version).values(status="active"))
    session.commit()
