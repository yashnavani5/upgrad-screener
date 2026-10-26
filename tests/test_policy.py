from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from screener.models import Base
from screener.policy import Item, activate, active, apply, default_policy, diff, effective_config, save
from screener.score.fit import default_categories
from screener.score.gates import policy_filter
from screener.score.pairwise import PairResult
from screener.score.service import judge_answered


def test_default_policy_reproduces_the_yaml(cfg) -> None:
    out = apply(cfg, default_policy(cfg)).weights
    w = cfg.weights
    assert out["track_record"]["weights"] == w["track_record"]["weights"]
    assert out["final"]["exponents"] == w["final"]["exponents"]
    assert out["pairwise"]["blend"] == pytest.approx(w["pairwise"]["blend"])
    assert out["mmr"]["shortlist_max"] == w["mmr"]["shortlist_max"]
    assert [c["id"] for c in out["fit"]["categories"]] == [c["id"] for c in default_categories(w["fit"])]
    assert out["policy"]["filters"]["require_education"] is True


def test_apply_never_mutates_the_yaml_config(cfg) -> None:
    before = copy.deepcopy((cfg.weights, cfg.exclusions, cfg.gap_map))
    p = default_policy(cfg)
    p.weights.momentum, p.lists.competitors = 0.9, ["Acme"]
    apply(cfg, p)
    assert (cfg.weights, cfg.exclusions, cfg.gap_map) == before


def test_lists_flow_into_gap_map_and_exclusions(cfg) -> None:
    p = default_policy(cfg)
    p.lists.wanted_capabilities.append(Item(id="ai_mock_interviews", name="AI mock interviews"))
    p.lists.competitors = ["Acme Learning"]
    p.categories[0].label = "Buy"
    p.categories[1].enabled = False
    out = apply(cfg, p)
    assert {"id": "ai_mock_interviews", "name": "AI mock interviews"} in out.gap_map["wanted_capabilities"]
    assert out.exclusions["competitors_at_scale"] == ["Acme Learning"]
    cats = out.weights["fit"]["categories"]
    assert cats[0]["label"] == "Buy" and p.categories[1].id not in {c["id"] for c in cats}


def test_diff_lists_changed_paths(cfg) -> None:
    a = default_policy(cfg)
    b = a.model_copy(deep=True)
    b.filters.max_headcount = 500
    paths = [d["path"] for d in diff(a, b)]
    assert any("max_headcount" in p for p in paths) and len(paths) == 1


def test_save_activate_and_rollback(cfg) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert active(s, cfg)[1] is None  # no saved version: YAML defaults
        p1 = default_policy(cfg)
        assert save(s, cfg, p1, author="test", note="baseline", activate=True) == 1
        p2 = p1.model_copy(deep=True)
        p2.selection.shortlist_size = 12
        assert save(s, cfg, p2, author="test", note="smaller", activate=True) == 2
        assert active(s, cfg)[1] == 2
        assert effective_config(s, cfg).weights["mmr"]["shortlist_max"] == 12
        assert save(s, cfg, p1, author="test", note="draft", activate=False) == 3
        assert active(s, cfg)[1] == 2  # a draft does not change the active policy
        activate(s, 1)  # rollback
        assert active(s, cfg)[1] == 1
        assert effective_config(s, cfg).weights["mmr"]["shortlist_max"] == cfg.weights["mmr"]["shortlist_max"]
        with pytest.raises(ValueError):
            activate(s, 99)


def _scored(name: str, geo: str) -> SimpleNamespace:
    return SimpleNamespace(cd=SimpleNamespace(aliases=[name], geo=geo, founded_year=None))


def test_policy_filter_blocklist_and_regions() -> None:
    assert policy_filter(_scored("Acme Learning", "india"), {}, ["acme learning"]) == "On the policy blocklist."
    why = policy_filter(_scored("Beta", "outside"), {"regions_allowed": {"india": True, "outside": False}}, [])
    assert why and "outside" in why


def test_judge_fallback_when_gemini_does_not_answer() -> None:
    ok = PairResult("a", "b", "a", "b", "", "")
    dead = PairResult("a", "c", None, None, "", "")
    assert judge_answered([], 0.5)
    assert judge_answered([ok, ok, dead], 0.5)
    assert not judge_answered([ok, dead, dead, dead], 0.5)
