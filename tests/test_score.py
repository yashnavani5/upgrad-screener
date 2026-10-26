"""Scoring math (SPEC section 3): percentile, shrinkage, Final, momentum, fit lenses, novelty, MMR, BT."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from screener.config import load_yaml
from screener.score import core
from screener.score.fit import FitInput, best_lens, lens_scores
from screener.score.mmr import MmrItem, select
from screener.score.novelty import cluster, cluster_growth, crowd_distance, quarter_index
from screener.score.pairwise import PairResult, closest_pairs, fit_bradley_terry, neighbour_pairs

W = load_yaml("weights")
GAP = load_yaml("upgrad_gap_map")


# --- percentile, money, decay ------------------------------------------------------------------------


def test_percentile_ranks_hand_computed() -> None:
    # n = 4 present values; ranks 1..4 -> (r - 0.5) / 4
    assert core.percentile_ranks([10, 30, 20, 40]) == [0.125, 0.625, 0.375, 0.875]


def test_percentile_ties_and_missing() -> None:
    # 5 and 5 tie for ranks 1,2 -> average 1.5 -> (1.5-0.5)/3; None is not in the pool
    assert core.percentile_ranks([5, None, 5, 9]) == [pytest.approx(1 / 3), None, pytest.approx(1 / 3), pytest.approx(2.5 / 3)]
    assert core.percentile_ranks([None, None]) == [None, None]
    assert core.percentile_ranks([7]) == [0.5]


def test_log_money_before_ranking_changes_nothing_in_order_but_spacing() -> None:
    raw = [1e5, 1e6, 1e9]
    assert core.percentile_ranks([core.log_money(x) for x in raw]) == core.percentile_ranks(raw)
    assert core.log_money(0) == 0.0 and core.log_money(None) is None and core.log_money(-5) is None


def test_recency_half_life() -> None:
    assert core.recency_weight(0, 180) == 1.0
    assert core.recency_weight(180, 180) == pytest.approx(0.5)
    assert core.recency_weight(360, 180) == pytest.approx(0.25)
    assert core.recency_weight(None, 180) == 0.0


# --- T, M, I, shrinkage, C, Final --------------------------------------------------------------------


def test_track_record_hand_computed() -> None:
    w = W["track_record"]["weights"]
    founder = {"prior_exit": 1, "prior_vc_backed": 0, "top_investor": 1, "scale_pct_log": 0.75,
               "years_in_edu_pct": 0.5, "operator_pedigree": 0, "accelerator_or_award": 1}
    # 0.25*1 + 0.20*0 + 0.15*1 + 0.15*0.75 + 0.10*0.5 + 0.10*0 + 0.05*1 = 0.6125
    assert core.track_record_founder(founder, w) == pytest.approx(0.6125)
    weak = {k: 0 for k in founder}
    assert core.track_record([founder, weak], w, team_depth=False, bonus=0.05, cap=1.0) == pytest.approx(0.6125)
    assert core.track_record([founder, weak], w, team_depth=True, bonus=0.05, cap=1.0) == pytest.approx(0.6625)
    strong = {k: 1 for k in founder}
    assert core.track_record([strong], w, team_depth=True, bonus=0.05, cap=1.0) == 1.0  # capped
    assert core.track_record([], w, True, 0.05, 1.0) == 0.0


def test_momentum_and_interesting() -> None:
    # (pct 0.8, fresh) and (pct 0.6, one half-life old) -> mean(0.8, 0.3) = 0.55
    assert core.momentum([(0.8, 1.0), (0.6, 0.5)]) == pytest.approx(0.55)
    assert core.momentum([]) is None
    assert core.interesting(0.55, 0.2, 0.5, 0.5) == pytest.approx(0.375)
    assert core.interesting(None, 0.2, 0.5, 0.5) == pytest.approx(0.1)


def test_shrinkage_hand_computed() -> None:
    # (n*s + k*mu) / (n + k), k = 3
    assert core.shrink(0.9, 1, 0.3, 3) == pytest.approx(0.45)
    assert core.shrink(0.9, 9, 0.3, 3) == pytest.approx(0.75)
    assert core.shrink(0.9, 0, 0.3, 3) == pytest.approx(0.3)  # no evidence -> pool mean


def test_confidence() -> None:
    assert core.confidence(9, 18) == 0.5
    assert core.confidence(30, 18) == 1.0
    assert core.confidence(5, 0) == 0.0


def test_final_formula_hand_computed() -> None:
    exps, eps = W["final"]["exponents"], W["final"]["epsilon"]
    # 0.5^0.3 * 0.4^0.3 * 0.8^0.3 * 0.6^0.1 = 0.81225 * 0.75966 * 0.93525 * 0.95020 = 0.54834
    assert core.final_score(0.5, 0.4, 0.8, 0.6, exps, eps) == pytest.approx(0.548342, abs=1e-6)
    # T = 0 is floored at epsilon 0.01 before exponentiation (not zeroed out)
    assert core.final_score(0.0, 0.4, 0.8, 0.6, exps, eps) == pytest.approx(0.169574, abs=1e-6)


def test_blend() -> None:
    assert core.blend(0.6, 0.9, 0.7, 0.3) == pytest.approx(0.69)
    assert core.blend(0.6, None, 0.7, 0.3) == pytest.approx(0.42)


def test_kendall_tau() -> None:
    assert core.kendall_tau(["a", "b", "c"], {"a": 3, "b": 2, "c": 1}) == pytest.approx(1.0)
    assert core.kendall_tau(["a", "b", "c"], {"a": 1, "b": 2, "c": 3}) == pytest.approx(-1.0)


# --- fit lenses --------------------------------------------------------------------------------------


def test_fit_lenses() -> None:
    conf = W["fit"]
    ai = FitInput(capabilities={"ai_tutoring", "assessment_proctoring"}, geo="india", total_raised_usd=5e6)
    s = lens_scores(ai, GAP, conf)
    # $5M raised: early but not very early -> acquire full, co-build at the early factor
    assert s["acquire"] == 1.0 and s["partner"] == 0.0 and s["co_build"] == pytest.approx(0.75)
    big = FitInput(capabilities={"ai_tutoring", "assessment_proctoring"}, geo="india", total_raised_usd=2e8)
    assert lens_scores(big, GAP, conf)["acquire"] == 0.0  # above the size ceiling
    partner = FitInput(segments={"study_abroad", "test_prep"}, capabilities={"study_abroad_finance_visa"}, geo="india",
                       total_raised_usd=3e7, stages=["Series B"])
    p = lens_scores(partner, GAP, conf)
    assert p["partner"] == pytest.approx(1.0 * (0.5 + 0.5 * 0.5)) and best_lens(p)[0] == "partner"
    unknown_geo = lens_scores(FitInput(capabilities={"ai_tutoring", "assessment_proctoring"}), GAP, conf)
    assert unknown_geo["acquire"] == pytest.approx(0.6 * 0.7)  # unknown location x unknown size
    known_by_headcount = FitInput(capabilities={"ai_tutoring", "assessment_proctoring"}, geo="india", size_known=True)
    assert lens_scores(known_by_headcount, GAP, conf)["acquire"] == 1.0


def test_very_early_companies_are_co_build_not_acquire() -> None:
    conf = W["fit"]
    caps = {"ai_tutoring", "assessment_proctoring"}
    for fi in (FitInput(capabilities=caps, geo="india", total_raised_usd=1.5e6),      # under $3M
               FitInput(capabilities=caps, geo="india", stages=["Pre-seed"]),          # stage only
               FitInput(capabilities=caps, geo="india", age_years=2)):                 # age only
        s = lens_scores(fi, GAP, conf)
        assert best_lens(s)[0] == "co_build" and s["co_build"] == 1.0 and s["acquire"] < s["co_build"]
    # raised beats age: a 2-year-old company with $8M raised is not very early
    s = lens_scores(FitInput(capabilities=caps, geo="india", total_raised_usd=8e6, age_years=2), GAP, conf)
    assert best_lens(s)[0] == "acquire"


def test_co_build_boost_for_strong_founder_at_early_company() -> None:
    conf = W["fit"]
    base = FitInput(capabilities={"ai_tutoring"}, geo="india", stages=["Seed"], founder_t=0.3)
    strong = FitInput(capabilities={"ai_tutoring"}, geo="india", stages=["Seed"], founder_t=0.45)
    later = FitInput(capabilities={"ai_tutoring"}, geo="india", stages=["Series B"], total_raised_usd=4e7, founder_t=0.7)
    assert lens_scores(base, GAP, conf)["co_build"] == pytest.approx(0.5)
    assert lens_scores(strong, GAP, conf)["co_build"] == pytest.approx(0.7)
    assert lens_scores(later, GAP, conf)["co_build"] == pytest.approx(0.25)


# --- novelty -----------------------------------------------------------------------------------------


def test_clustering_and_crowd_distance() -> None:
    rng = np.random.default_rng(0)
    crowded = rng.normal([1, 0, 0], 0.02, size=(12, 3))
    niche = rng.normal([0, 1, 0], 0.02, size=(4, 3))
    x = np.vstack([crowded, niche])
    c = cluster(x, 2, 6, 3, 0.5, 42)
    assert c.k >= 2
    big = int(np.bincount(c.labels).argmax())
    assert big in c.crowded
    d = crowd_distance(x, c)
    assert d[:12].mean() < 0.05 and d[12:].mean() > 0.5  # niche items are far from crowded centroids


def test_cluster_growth_slope() -> None:
    today = date(2026, 10, 25)
    assert quarter_index(date(2026, 10, 1), today) == 0      # Q4 2026 = current quarter
    assert quarter_index(date(2026, 8, 1), today) == 1       # Q3
    assert quarter_index(date(2026, 6, 1), today) == 2       # Q2
    labels = np.array([0, 0, 1])
    rounds = [[date(2026, 9, 1), date(2026, 7, 15)], [date(2026, 8, 1)], [date(2024, 12, 1)]]
    g = cluster_growth(labels, rounds, today, 8)
    assert g[0] > 0          # 3 rounds, all in the latest quarters -> rising
    assert g[1] == 0.0       # Dec 2024 is 8 quarters back: outside the window -> no counts -> flat


# --- MMR ---------------------------------------------------------------------------------------------


def test_mmr_prefers_diversity() -> None:
    items = [MmrItem("a", 0.90, "acquire", [1, 0]), MmrItem("a2", 0.89, "acquire", [1, 0.01]),
             MmrItem("b", 0.80, "acquire", [0, 1])]
    picked = [i.key for i in select(items, 2, 0.75, 0.1, 0, ["acquire", "partner", "co_build"])]
    assert picked == ["a", "b"]  # a2 is nearly identical to a; b is diverse


def test_mmr_guarantees_min_per_fit_type_when_pool_allows() -> None:
    items = [MmrItem(f"q{i}", 0.9 - i * 0.01, "acquire", [1, i * 0.1]) for i in range(10)] + [
        MmrItem("p1", 0.3, "partner", [0, 1]), MmrItem("p2", 0.2, "partner", [0.1, 1]),
        MmrItem("c1", 0.25, "co_build", [1, 1])]
    picked = select(items, 5, 0.75, 0.1, 2, ["acquire", "partner", "co_build"])
    types = [i.fit_type for i in picked]
    assert types.count("partner") == 2 and types.count("co_build") == 1 and len(picked) == 5  # only 1 co_build exists


def test_mmr_reserves_india_slots_when_pool_allows() -> None:
    # Foreign companies score higher, but at least 3 of 5 slots must be India-located (only 3 exist).
    foreign = [MmrItem(f"f{i}", 0.9 - i * 0.01, "acquire", [1, i * 0.1], "outside") for i in range(6)]
    india = [MmrItem(f"i{i}", 0.5 - i * 0.01, "acquire", [i * 0.1, 1], "india") for i in range(3)]
    picked = select(foreign + india, 5, 0.75, 0.1, 0, ["acquire", "partner", "co_build"], {"india": 3})
    assert sum(1 for x in picked if x.region == "india") == 3 and len(picked) == 5
    capped = select(foreign + india[:1], 5, 0.75, 0.1, 0, ["acquire"], {"india": 3})
    assert sum(1 for x in capped if x.region == "india") == 1  # only one exists: the minimum is capped


def test_mmr_region_and_fit_minimums_together() -> None:
    items = [MmrItem("fa", 0.9, "acquire", [1, 0], "outside"), MmrItem("fb", 0.89, "acquire", [0.9, 0.1], "outside"),
             MmrItem("ia", 0.4, "acquire", [0, 1], "india"), MmrItem("ip", 0.3, "partner", [0.2, 1], "india")]
    picked = select(items, 3, 0.75, 0.1, 1, ["acquire", "partner"], {"india": 2})
    keys = {x.key for x in picked}
    assert {"ia", "ip"} <= keys  # both India slots, and ip also covers the partner minimum


def test_mmr_pinned_reviewer_picks_take_slots_first() -> None:
    items = [MmrItem(f"top{i}", 0.9 - i * 0.01, "acquire", [1, i * 0.1]) for i in range(5)] + [
        MmrItem("pick", 0.2, "partner", [0, 1]), MmrItem("absent_pick_is_ignored", 0.1, "acquire", [0, 1])]
    picked = [x.key for x in select(items[:-1], 3, 0.75, 0.1, 0, ["acquire", "partner"], pinned={"pick", "not_in_pool"})]
    assert picked[0] == "pick" and len(picked) == 3 and picked[1] == "top0"  # pin first, then MMR as usual
    capped = select(items, 1, 0.75, 0.1, 0, ["acquire"], pinned={"pick", "absent_pick_is_ignored"})
    assert [x.key for x in capped] == ["pick"]  # more pins than slots: best-scored pins win


# --- Bradley-Terry -----------------------------------------------------------------------------------


def test_bt_toy_case() -> None:
    items = ["a", "b", "c"]
    # a beats b and c in both orders; b beats c in both orders
    res = [PairResult("a", "b", "A", "B"), PairResult("a", "c", "A", "B"), PairResult("b", "c", "A", "B")]
    s = fit_bradley_terry(items, res, 0.01)
    assert s["a"] > s["b"] > s["c"]


def test_bt_disagreement_is_a_tie() -> None:
    r = PairResult("a", "b", "A", "A")  # first order says a, swapped order says b -> tie
    assert r.outcome is None
    s = fit_bradley_terry(["a", "b"], [r], 0.01)
    assert s["a"] == pytest.approx(s["b"], abs=1e-6)


def test_adaptive_pair_schedule() -> None:
    order = list("abcdefgh")
    first = neighbour_pairs(order, 3)
    assert ("a", "b") in first and ("a", "e") not in first
    strengths = {x: float(i) for i, x in enumerate(order)}
    more = closest_pairs(strengths, 4, {frozenset(p) for p in first})
    assert all(frozenset(p) not in {frozenset(q) for q in first} for p in more)


# --- gates -------------------------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from screener.score.gates import evaluate  # noqa: E402
from screener.scouts.prefilter import Prefilter  # noqa: E402

EXCL = load_yaml("exclusions")
PF = Prefilter(load_yaml("sources")["prefilter_keywords"])


def claim(field: str, value: dict, url: str = "https://inc42.com/x") -> SimpleNamespace:
    return SimpleNamespace(field=field, value_json=value, source_url=url, verified_status="supported", meta={}, id=field)


def company_data(name: str, *, domain: str | None = "acme.in", raised=None, status="unknown", identity=True,
                 claims=None, founder_claims=None, segments=("test_prep",)) -> SimpleNamespace:
    founder = SimpleNamespace(founder=SimpleNamespace(identity_verified=identity),
                              claims=founder_claims if founder_claims is not None else
                              [claim("current_role", {"company": name, "role": "Founder"}, "https://acme.in/team")])
    return SimpleNamespace(
        company=SimpleNamespace(name=name, domain=domain, total_raised_usd=raised, status=status, status_reason="x"),
        aliases=[name], claims=claims if claims is not None else [
            claim("description", {"text": "test prep for students"}, "https://acme.in/"),
            claim("funding_round", {}), claim("latest_news_date", {}), claim("open_roles", {"count": 3})],
        founders=[founder], segments=set(segments), capabilities=set())


def test_gates_pass_and_shortlist_ok() -> None:
    g = evaluate(company_data("Acme Learning"), EXCL, PF, 3)
    assert g.passed and g.shortlist_ok and g.reason_code is None


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"name": "upGrad"}, "UPGRAD_OWNED"),
        ({"name": "upGrad Abroad"}, "UPGRAD_OWNED"),
        ({"name": "Unacademy"}, "COMPETITOR"),
        ({"name": "Acme Learning", "raised": 2e8}, "TOO_LARGE"),
        ({"name": "Acme Learning", "segments": (), "claims": [claim("description", {"text": "payments for shops"})]}, "NOT_EDUCATION"),
        ({"name": "Acme Learning", "status": "inactive"}, "INACTIVE"),
        ({"name": "Acme Learning", "identity": False}, "UNVERIFIABLE"),
    ],
)
def test_gate_failures(kwargs: dict, code: str) -> None:
    name = kwargs.pop("name")
    g = evaluate(company_data(name, **kwargs), EXCL, PF, 3)
    assert not g.passed and g.reason_code == code


def test_shortlist_gate_needs_three_signal_claims_and_non_linkedin_role() -> None:
    few = company_data("Acme Learning", claims=[claim("description", {"text": "tutoring"}, "https://acme.in/")])
    g = evaluate(few, EXCL, PF, 3)
    assert g.passed and not g.shortlist_ok and "verified track-record/interest claims" in g.explanation
    linkedin_only = company_data("Acme Learning", founder_claims=[
        claim("current_role", {"company": "Acme Learning", "role": "Founder"}, "https://in.linkedin.com/in/x")])
    g2 = evaluate(linkedin_only, EXCL, PF, 3)
    assert not g2.shortlist_ok and "current role" in g2.explanation


# --- India-first location evidence, size inputs, lookup selection -------------------------------------

from screener.enrich.lookups import needs  # noqa: E402
from screener.score.features import infer_geo, size_inputs  # noqa: E402

REG = ["tofler.in", "zaubacorp.com"]


def gclaim(field: str, value: dict, span: str = "", url: str = "https://inc42.com/x", entity: str = "company",
           status: str = "supported") -> SimpleNamespace:
    return SimpleNamespace(field=field, value_json=value, evidence_span=span, source_url=url, entity_type=entity,
                           verified_status=status, meta={}, id=field)


def test_infer_geo_strongest_evidence_first() -> None:
    assert infer_geo([gclaim("country", {"country": "United Kingdom"})], "acme.in", REG) == ("outside", "country/city claim")
    assert infer_geo([gclaim("city", {"city": "Bengaluru"})], None, REG)[0] == "india"
    assert infer_geo([gclaim("legal_name", {"name": "Acme Pvt Ltd"}, url="https://www.tofler.in/acme")], None, REG) == (
        "india", "Indian company registry listing")
    geo, basis = infer_geo([gclaim("funding_round", {}, "Indore-based edtech startup Arivihan has raised $10 million")], None, REG)
    assert geo == "india" and "Indore-based" in basis
    assert infer_geo([gclaim("description", {}, "The startup is headquartered in Pune.")], None, REG)[0] == "india"
    assert infer_geo([gclaim("revenue", {"currency_original": "INR"})], None, REG) == ("india", "amount stated in INR")
    assert infer_geo([], "acme.co.in", REG) == ("india", "website on a .in domain")
    # "expanding to India" is not evidence that the company is Indian
    assert infer_geo([gclaim("description", {}, "Acme plans to expand to India next year")], "acme.com", REG)[0] == "unknown"


def size_cd(raised=None, claims=(), headcount=None) -> SimpleNamespace:
    cl = list(claims) + ([gclaim("headcount", {"text": headcount})] if headcount else [])
    return SimpleNamespace(company=SimpleNamespace(total_raised_usd=raised), all_claims=cl, claims=cl)


def test_size_inputs_take_the_largest_verified_figure() -> None:
    assert size_inputs(size_cd()) == (None, None, False)
    assert size_inputs(size_cd(headcount="45 employees")) == (None, None, True)          # known by headcount
    assert size_inputs(size_cd(raised=5e6)) == (5e6, "corroborated funding rounds", True)
    big = size_cd(raised=5e6, claims=[gclaim("total_funding", {"amount_usd": 4e8})])     # Degreed-like case
    assert size_inputs(big)[:2] == (4e8, "total funding statement (single source)")
    rnd = size_cd(claims=[gclaim("funding_round", {"amount_usd": 2e7}), gclaim("funding_round", {"amount_usd": 3e6})])
    assert size_inputs(rnd)[:2] == (2e7, "largest funding round (single source)")
    ignored = size_cd(claims=[gclaim("total_funding", {"amount_usd": 9e9}, status="not_found")])
    assert size_inputs(ignored)[0] is None                                               # only supported claims count


def test_gate_uses_single_source_size_evidence() -> None:
    cd = company_data("Acme Learning")
    cd.raised_usd, cd.raised_basis = 4e8, "total funding statement (single source)"
    g = evaluate(cd, EXCL, PF, 3)
    assert g.reason_code == "TOO_LARGE" and "single source" in g.explanation


def test_lookup_needs_only_missing_and_not_yet_done() -> None:
    f1 = SimpleNamespace(founder=SimpleNamespace(lookups={}), claims=[])
    f2 = SimpleNamespace(founder=SimpleNamespace(lookups={"background": "2026-10-25"}), claims=[])
    f3 = SimpleNamespace(founder=SimpleNamespace(lookups={}), claims=[gclaim("prior_role", {})])
    cd = SimpleNamespace(company=SimpleNamespace(lookups={"location": "2026-10-25"}), size_known=False, geo="unknown",
                         founders=[f1, f2, f3])
    todo, founders, x = needs(cd, 3)
    assert todo == ["size", "x"] and founders == [f1.founder]  # location done; f2 done; f3 already has background
    assert x == [f1.founder, f2.founder, f3.founder]           # no X lookup done yet for anyone
    known = SimpleNamespace(company=SimpleNamespace(lookups={"x": "2026-10-25"}), size_known=True, geo="india", founders=[])
    assert needs(known, 2) == ([], [], [])


def test_lookup_background_v2_reruns_once_for_thin_founders() -> None:
    thin = SimpleNamespace(founder=SimpleNamespace(lookups={"background": "2026-10-25"}), claims=[gclaim("education", {})])
    rich = SimpleNamespace(founder=SimpleNamespace(lookups={"background": "2026-10-25"}),
                           claims=[gclaim("prior_role", {}), gclaim("education", {})])
    done = SimpleNamespace(founder=SimpleNamespace(lookups={"background": "2026-10-25", "background_v2": "2026-10-27"}), claims=[])
    cd = SimpleNamespace(company=SimpleNamespace(lookups={"size": "x", "location": "x", "x": "x"}), size_known=True,
                         geo="india", founders=[thin, rich, done])
    _, founders, _ = needs(cd, 3, background_version=2, min_background=2)
    assert founders == [thin.founder]                     # rich has 2 facts; done already ran v2
    assert needs(cd, 3)[1] == []                           # v1 rules: all three already looked up once


def test_acquired_company_is_excluded() -> None:
    acq = SimpleNamespace(field="acquired", value_json={"acquirer": "Top Hat", "date": "2022-12"}, verified_status="supported",
                          entity_type="company", source_url="https://x.example", meta={}, id="acq")
    cd = company_data("Acme Learning")
    cd.all_claims = [acq]
    g = evaluate(cd, EXCL, PF, 3)
    assert not g.passed and g.reason_code == "ACQUIRED" and "Top Hat" in g.explanation
