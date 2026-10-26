"""`screener score`: deterministic scores from verified claims -> gates -> pairwise judge (BT) -> MMR shortlist.
Everything is persisted with a run_id (runs, candidates, scores, exclusions, run_funnel, judgments)."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from screener.models import (
    Candidate, Claim, Company, Discovery, Exclusion, Judgment, Run, RunFunnel, Score,
)
from screener.resolve.normalize import registered_domain
from screener.score import core
from screener.score.features import CompanyData, load, usable
from screener.score.fit import FitInput, best_lens, categories, lens_scores
from screener.score.gates import GateResult, evaluate, on_list, policy_filter
from screener.score.mmr import MmrItem, select as mmr_select
from screener.score.novelty import cluster, cluster_growth, crowd_distance, novelty
from screener.scouts.prefilter import Prefilter
from screener.verify.verifier import statement

log = logging.getLogger(__name__)

EVIDENCE_PRIORITY = ("funding_round", "revenue", "acquired", "prior_exit", "description", "segment", "capability",
                     "legal_name", "latest_news_date", "open_roles", "github_stars")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Scored:
    cd: CompanyData
    t_raw: float = 0.0
    m: float | None = None
    n_nov: float | None = None
    i_raw: float = 0.0
    lenses: dict[str, float] = field(default_factory=dict)
    f_raw: float = 0.0
    fit_type: str = "acquire"
    n_t: int = 0
    n_i: int = 0
    n_f: int = 0
    t: float = 0.0
    i: float = 0.0
    f: float = 0.0
    c: float = 0.0
    final: float = 0.0
    bt: float | None = None
    bt_pct: float | None = None
    final_prime: float = 0.0
    rank: int | None = None
    shortlist_rank: int | None = None
    gate: GateResult | None = None
    founder_features: list[dict[str, float]] = field(default_factory=list)
    pool_means: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> Any:
        return self.cd.key


def compute(datas: list[CompanyData], weights: dict[str, Any], gap_map: dict[str, Any],
            embeddings: dict[Any, list[float]], today: date) -> list[Scored]:
    """SPEC 3.1-3.8 for the whole pool. Pure: no I/O, no LLM."""
    tr, intr, fit_conf = weights["track_record"], weights["interesting"], weights["fit"]
    out = [Scored(cd) for cd in datas]

    # T: founder-level percentiles within the pool of founders, then max over co-founders (+ team depth).
    founders = [(s, fd) for s in out for fd in s.cd.founders]
    scale_pct = core.percentile_ranks([fd.raw.get("scale") for _, fd in founders])
    years_pct = core.percentile_ranks([fd.raw.get("years_in_edu") for _, fd in founders])
    feats: dict[int, list[dict[str, float]]] = {}
    for idx, ((s, fd), sp, yp) in enumerate(zip(founders, scale_pct, years_pct, strict=True)):
        f = {"prior_exit": fd.raw["prior_exit"], "prior_vc_backed": fd.raw["prior_vc_backed"],
             "top_investor": fd.raw["top_investor"], "scale_pct_log": sp or 0.0, "years_in_edu_pct": yp or 0.0,
             "operator_pedigree": fd.raw["operator_pedigree"], "accelerator_or_award": fd.raw["accelerator_or_award"]}
        feats.setdefault(id(s), []).append(f)
    for s in out:
        fs = feats.get(id(s), [])
        strong = sum(1 for f in fs if any(v > 0 for v in f.values()))
        s.founder_features = fs
        s.t_raw = core.track_record(fs, tr["weights"], strong >= 2, float(tr["team_depth_bonus"]), float(tr["cap"]))
        s.n_t = max((fd.signals for fd in s.cd.founders), default=0)

    # M: momentum percentiles x recency decay, averaged over available features.
    half_life = float(intr["momentum"]["recency_half_life_days"])
    parts: dict[int, list[tuple[float, float]]] = {id(s): [] for s in out}
    for feature in sorted({k for s in out for k in s.cd.momentum_raw}):
        vals = [s.cd.momentum_raw.get(feature, (None, None))[0] for s in out]
        for s, p in zip(out, core.percentile_ranks(vals), strict=True):
            if p is not None:
                parts[id(s)].append((p, core.recency_weight(s.cd.momentum_raw[feature][1], half_life)))
    for s in out:
        s.m = core.momentum(parts[id(s)])
        s.n_i = len(parts[id(s)])

    # N: novelty from description embeddings.
    nov = intr["novelty"]
    have = [s for s in out if embeddings.get(s.key) is not None]
    if len(have) >= 3:
        x = np.asarray([embeddings[s.key] for s in have], dtype=float)
        cl = cluster(x, int(nov["k_min"]), int(nov["k_max"]), int(nov["min_points_per_cluster"]),
                     float(nov["crowded_quantile"]), int(nov["random_state"]))
        growth = cluster_growth(cl.labels, [s.cd.round_dates for s in have], today, int(nov["cluster_growth_window_quarters"]))
        for s, v in zip(have, novelty(crowd_distance(x, cl), cl.labels, growth), strict=True):
            s.n_nov = v
            s.n_i += 1

    for s in out:
        s.i_raw = core.interesting(s.m, s.n_nov, float(intr["weights"]["momentum"]), float(intr["weights"]["novelty"]))
        fi = FitInput(s.cd.segments, s.cd.capabilities, s.cd.geo, s.cd.raised_usd, s.cd.stages, s.t_raw,
                      size_known=s.cd.size_known,
                      age_years=(today.year - s.cd.founded_year) if s.cd.founded_year else None)
        s.lenses = lens_scores(fi, gap_map, fit_conf)
        s.fit_type, s.f_raw = best_lens(s.lenses)
        s.n_f = s.cd.n_fit

    # Shrinkage toward the pool mean, confidence, Final.
    k = float(weights["shrinkage"]["k"])
    means = {"T": float(np.mean([s.t_raw for s in out])) if out else 0.0,
             "I": float(np.mean([s.i_raw for s in out])) if out else 0.0,
             "F": float(np.mean([s.f_raw for s in out])) if out else 0.0}
    fin = weights["final"]
    for s in out:
        s.pool_means = means
        s.t = core.shrink(s.t_raw, s.n_t, means["T"], k)
        s.i = core.shrink(s.i_raw, s.n_i, means["I"], k)
        s.f = core.shrink(s.f_raw, s.n_f, means["F"], k)
        s.c = core.confidence(s.cd.verified_claims, _expected(s.cd, weights), float(weights["confidence"]["cap"]))
        s.final = core.final_score(s.t, s.i, s.f, s.c, fin["exponents"], float(fin["epsilon"]))
        s.final *= float((fin.get("region_multiplier") or {}).get(s.cd.geo, 1.0))  # India-first
        s.final_prime = s.final
    return out


def _expected(cd: CompanyData, weights: dict[str, Any]) -> float:
    ec = weights["confidence"]["expected_claims"]
    return float(ec["company"]) + float(ec["founder"]) * min(len(cd.founders), int(ec["max_founders"]))


# --- dossiers for the judge (verified claims only) --------------------------------------------------


def dossier(s: Scored) -> str:
    cd = s.cd
    lines = [f"Company: {cd.company.name}" + (f" ({cd.company.domain})" if cd.company.domain else "")]
    for c in sorted(cd.claims, key=lambda c: c.field):
        if c.field in ("website", "latest_news_date"):
            continue
        lines.append("- " + statement(c.field, c.value_json or {}, cd.company.name))
    for fd in cd.founders:
        fc = [c for c in fd.claims if usable(c)]
        if fc:
            lines.append(f"Founder {fd.founder.full_name}:")
            lines += ["  - " + statement(c.field, c.value_json or {}, fd.founder.full_name) for c in fc[:8]]
    text = "\n".join(lines)
    return text[:3000] if len(lines) > 1 else text + "\n(no verified facts)"


def gap_map_text(gap: dict[str, Any]) -> str:
    units = "; ".join(b["name"] for b in gap.get("business_units", []))
    wanted = "; ".join(c["name"] for c in gap.get("wanted_capabilities", []))
    return f"Existing business units: {units}.\nWanted capabilities: {wanted}."


def evidence(cd: CompanyData) -> list[dict[str, str]]:
    out, seen = [], set()
    for fname in EVIDENCE_PRIORITY:
        for c in sorted((c for c in cd.claims if c.field == fname), key=lambda c: -(c.verifier_confidence or 0)):
            domain = registered_domain(c.source_url) or ""
            if domain in seen:
                continue
            seen.add(domain)
            out.append({"url": c.source_url, "title": None, "domain": domain})
            if len(out) == 3:
                return out
    return out


# --- run ---------------------------------------------------------------------------------------------


def judge_answered(results: list[Any], min_share: float) -> bool:
    """True when enough pairwise judgments came back (either order) to fit Bradley-Terry. Pure."""
    if not results:
        return True
    answered = sum(1 for r in results if r.winner_ab is not None or r.winner_ba is not None)
    return answered >= min_share * len(results)


def stored_bt_pct(session: Session) -> dict[Any, float]:
    """company_id -> Bradley-Terry percentile from the newest scored run (the AI Policy preview uses the same)."""
    from screener.score.seeds import scored_run_id

    run_id = scored_run_id(session)
    if run_id is None:
        return {}
    return dict(session.execute(select(Candidate.company_id, Score.bt_pct).join(Score, Score.candidate_id == Candidate.id)
                                .where(Score.run_id == run_id, Score.bt_pct.is_not(None))).all())


@dataclass
class ScoreReport:
    run_id: uuid.UUID
    scored: list[Scored]
    shortlist: list[Scored]
    exclusions: list[tuple[Scored, str, str]]
    judgments: int = 0
    pairwise_items: int = 0
    judge_reused: bool = False  # the judge could not run; the last scored run's percentiles were reused


async def score(session: Session, cfg: Any, *, embedder: Any, gemini: Any, pairwise: bool = True,
                today: date | None = None, run: Run | None = None, finalize: bool = True,
                on_rows: Any = None) -> ScoreReport:
    """`run`: join an orchestrated run (else a new one). `finalize`: mark the run succeeded at the end.
    `on_rows(rows)`: called with batches of (Score, Company, Candidate) as they are persisted (Sheet streaming).
    A run created here is marked failed if scoring raises (an orchestrated run is handled by the orchestrator)."""
    if run is not None:
        return await _score(session, cfg, embedder=embedder, gemini=gemini, pairwise=pairwise, today=today, run=run,
                            finalize=finalize, on_rows=on_rows)
    run = Run(id=uuid.uuid4(), trigger="cli", status="running", started_at=utcnow())
    session.add(run)
    try:
        return await _score(session, cfg, embedder=embedder, gemini=gemini, pairwise=pairwise, today=today, run=run,
                            finalize=finalize, on_rows=on_rows)
    except Exception as exc:
        session.rollback()
        if (row := session.get(Run, run.id)) is not None:
            row.status, row.finished_at = "failed", utcnow()
            row.error_summary = f"score: {type(exc).__name__}: {exc}"[:2000]
            session.commit()
        raise


async def _score(session: Session, cfg: Any, *, embedder: Any, gemini: Any, pairwise: bool, today: date | None,
                 run: Run, finalize: bool, on_rows: Any) -> ScoreReport:
    today = today or utcnow().date()
    weights = cfg.weights
    run.current_stage, run.heartbeat_at = "score", utcnow()
    run.config_snapshot = cfg.snapshot()
    run.config_hash = hashlib.sha256(json.dumps(cfg.snapshot(), sort_keys=True, default=str).encode()).hexdigest()
    session.commit()

    companies = [c for c in session.scalars(select(Company).where(Company.verified_at.is_not(None)))]
    datas = load(session, cfg, companies, today)
    embeddings = await embed_companies(datas, embedder)
    scored = compute(datas, weights, cfg.gap_map, embeddings, today)

    prefilter = Prefilter(cfg.sources.get("prefilter_keywords", []))
    min_signals = int(weights["shortlist_gate"]["min_verified_signal_claims"])
    pol = weights.get("policy") or {}
    filters = pol.get("filters") or {}
    for s in scored:
        s.gate = evaluate(s.cd, cfg.exclusions, prefilter, min_signals, filters)

    # Pairwise judge over the top N gate-passers, both orders -> Bradley-Terry -> Final'.
    report = ScoreReport(run.id, scored, [], [])
    pw = weights["pairwise"]
    eligible = sorted([s for s in scored if s.gate.passed], key=lambda s: -s.final)
    top = eligible[: int(pw["top_n"])]
    if pairwise and gemini is not None and len(top) >= 2:
        from screener.score.pairwise import run_pairwise

        by_key = {s.key: s for s in top}
        strengths, results = await run_pairwise(gemini, [s.key for s in top], lambda k: dossier(by_key[k]),
                                                gap_map_text(cfg.gap_map), pw)
        if not judge_answered(results, float(pw["min_answered_share"])):
            # The judge could not run (e.g. the Gemini quota): reuse the last scored run's percentiles, exactly as
            # the AI Policy preview does, instead of a Bradley-Terry fit over mostly missing judgments.
            log.warning("pairwise judge answered too few of %d pairs; reusing stored percentiles", len(results))
            stored = stored_bt_pct(session)
            for s in top:
                s.bt, s.bt_pct = None, stored.get(s.key)
            results, report.judge_reused = [], True
        else:
            pct = core.percentile_ranks([strengths[s.key] for s in top])
            for s, p in zip(top, pct, strict=True):
                s.bt, s.bt_pct = strengths[s.key], p
        for r in results:
            session.add(Judgment(id=uuid.uuid4(), run_id=run.id, company_a=r.a, company_b=r.b, winner_ab=r.winner_ab,
                                 winner_ba=r.winner_ba, outcome=r.outcome, reason_ab=r.reason_ab[:2000],
                                 reason_ba=r.reason_ba[:2000], model=cfg.settings.gemini_model_smart))
        report.judgments, report.pairwise_items = len(results), len(top)
    blend = pw["blend"]
    for s in scored:
        s.final_prime = core.blend(s.final, s.bt_pct, float(blend["final"]), float(blend["bt_pct"]))
    for rank, s in enumerate(sorted(scored, key=lambda s: -s.final_prime), start=1):
        s.rank = rank
    # AI Policy filters that need the computed score (region, founded window, funding, stage, confidence, blocklist).
    for s in scored:
        if s.gate.passed and (why := policy_filter(s, filters, pol.get("blocklist") or [])):
            s.gate = GateResult(False, False, "POLICY_FILTER", why)

    # MMR shortlist over shortlist-eligible candidates.
    mm = weights["mmr"]
    pool = [s for s in scored if s.gate.shortlist_ok]
    lens_ids = [c["id"] for c in categories(weights["fit"])]
    picked = mmr_select([MmrItem(s.key, s.final_prime, s.fit_type, embeddings.get(s.key), s.cd.geo) for s in pool],
                        int(mm["shortlist_max"]), float(mm["lambda"]), float(mm["same_fit_type_sim_bonus"]),
                        int(mm["min_per_fit_type"]), lens_ids, {"india": int(mm.get("min_india", 0))},
                        pinned={s.key for s in pool if on_list(s.cd, pol.get("must_consider") or [])})
    by_key = {s.key: s for s in scored}
    for i, item in enumerate(picked, start=1):
        by_key[item.key].shortlist_rank = i
    report.shortlist = [by_key[i.key] for i in picked]

    # Exclusions: gate failures, then eligible-but-not-selected (DUPLICATE_THESIS / LOW_FIT near-misses).
    cutoff = min((s.final_prime for s in report.shortlist), default=float("inf"))
    for s in scored:
        if s.shortlist_rank is not None:
            continue
        if s.gate.reason_code:
            report.exclusions.append((s, s.gate.reason_code, s.gate.explanation))
        elif s.final_prime >= cutoff:
            closest = max(report.shortlist, key=lambda x: core.cosine(embeddings.get(s.key), embeddings.get(x.key)), default=None)
            report.exclusions.append((s, "DUPLICATE_THESIS", f"Ranked #{s.rank} by Final' but too similar to "
                                      f"{closest.cd.company.name if closest else 'a selected company'} (MMR diversity)."))
        else:
            report.exclusions.append((s, "LOW_FIT", f"Passed all gates; ranked #{s.rank} by Final' "
                                      f"({s.final_prime:.3f}), below the shortlist cut ({cutoff:.3f})."))

    persist(session, run, report, scored, today, finalize=finalize, on_rows=on_rows)
    return report


async def embed_companies(datas: list[CompanyData], embedder: Any) -> dict[Any, list[float]]:
    texts = [(d.key, d.text) for d in datas if d.text and len(d.text) >= 20]
    if not texts or embedder is None:
        return {}
    res = await embedder.embed([t for _, t in texts], task="CLUSTERING")
    return {k: v for (k, _), v in zip(texts, res.vectors, strict=True)}


def _breakdown(s: Scored, weights: dict[str, Any]) -> list[dict[str, Any]]:
    exps = weights["final"]["exponents"]

    def comp(name: str, raw: Any, adjusted: Any, n: int, mean: Any, weight: Any) -> dict[str, Any]:
        return {"name": name, "raw": _r(raw), "adjusted": _r(adjusted), "n_signals": n, "pool_mean": _r(mean),
                "weight": weight, "why_not_higher": None}

    w_i = weights["interesting"]["weights"]
    return [
        comp("T", s.t_raw, s.t, s.n_t, s.pool_means.get("T"), exps["T"]),
        comp("M", s.m, s.m, s.n_i, None, w_i["momentum"]),
        comp("N", s.n_nov, s.n_nov, 1 if s.n_nov is not None else 0, None, w_i["novelty"]),
        comp("I", s.i_raw, s.i, s.n_i, s.pool_means.get("I"), exps["I"]),
        *[comp(f"F_{k}", v, None, s.n_f, None, None) for k, v in s.lenses.items()],  # every AI Policy category
        comp("F", s.f_raw, s.f, s.n_f, s.pool_means.get("F"), exps["F"]),
        comp("C", s.c, s.c, s.cd.verified_claims, None, exps["C"]),
        comp("final", s.final, s.final, 0, None, None),
        comp("bt_pct", s.bt_pct, s.bt_pct, 0, None, weights["pairwise"]["blend"]["bt_pct"]),
        comp("final_prime", s.final_prime, s.final_prime, 0, None, None),
    ]


def _r(x: Any) -> Any:
    return round(float(x), 4) if isinstance(x, (int, float)) and x is not None else None


def persist(session: Session, run: Run, report: ScoreReport, scored: list[Scored], today: date, *,
            finalize: bool = True, on_rows: Any = None, stream_batch: int = 25) -> None:
    weights = run.config_snapshot["weights"]
    weights_version = f"v{weights.get('version')}:{run.config_hash[:8]}"
    excl = {s.key: (code, text) for s, code, text in report.exclusions}
    pending: list[tuple[Any, Any, Any]] = []
    for s in sorted(scored, key=lambda x: x.rank or 0):
        cand = Candidate(id=uuid.uuid4(), run_id=run.id, company_id=s.key,
                         founder_ids=[str(fd.founder.id) for fd in s.cd.founders], stage="scored",
                         gate_status="passed" if s.gate.passed else "failed")
        session.add(cand)
        session.flush()
        session.add(Score(
            id=uuid.uuid4(), candidate_id=cand.id, run_id=run.id, t=s.t, m=s.m, n=s.n_nov, i=s.i, f=s.f,
            f_acquire=s.lenses.get("acquire"), f_partner=s.lenses.get("partner"), f_co_build=s.lenses.get("co_build"),
            fit_type=s.fit_type, c=s.c, final=s.final, bt_score=s.bt, bt_pct=s.bt_pct, final_prime=s.final_prime,
            rank=s.rank, shortlist_rank=s.shortlist_rank,
            features={"founders": s.founder_features, "momentum": {k: list(v) for k, v in s.cd.momentum_raw.items()},
                      "segments": sorted(s.cd.segments), "capabilities": sorted(s.cd.capabilities), "geo": s.cd.geo,
                      "geo_basis": s.cd.geo_basis, "size_known": s.cd.size_known, "raised_usd": s.cd.raised_usd,
                      "raised_basis": s.cd.raised_basis, "verified_claims": s.cd.verified_claims,
                      "lenses": {k: round(v, 4) for k, v in s.lenses.items()}, "stages": s.cd.stages,
                      "founded_year": s.cd.founded_year, "policy_version": (weights.get("policy") or {}).get("version")},
            breakdown=_breakdown(s, weights), evidence=evidence(s.cd),
            weights_version=weights_version,
        ))
        if s.key in excl:
            code, text = excl[s.key]
            session.add(Exclusion(id=uuid.uuid4(), candidate_id=cand.id, run_id=run.id, reason_code=code,
                                  explanation=text, would_have_ranked=s.rank, source_claim_id=s.gate.source_claim_id,
                                  evidence_claim_ids=[]))
        if on_rows is not None:
            session.flush()
            pending.append((session.scalar(select(Score).where(Score.candidate_id == cand.id)), s.cd.company, cand))
            if len(pending) >= stream_batch:
                _stream(on_rows, pending)
                pending = []
    if on_rows is not None and pending:
        _stream(on_rows, pending)
    funnel = {
        "discovered": session.scalar(select(func.count()).select_from(Discovery)) or 0,
        "resolved": session.scalar(select(func.count()).select_from(Company)) or 0,
        "enriched": session.scalar(select(func.count()).select_from(Company).where(Company.enriched_at.is_not(None))) or 0,
        "verified": session.scalar(select(func.count()).select_from(Company).where(Company.verified_at.is_not(None))) or 0,
        "gated": sum(1 for s in scored if s.gate.passed),
        "scored": len(scored),
        "shortlisted": len(report.shortlist),
    }
    for order, (stage, count) in enumerate(funnel.items(), start=1):
        session.add(RunFunnel(run_id=run.id, stage=stage, stage_order=order, count=count))
    if finalize:
        run.status, run.current_stage, run.stage_progress = "succeeded", "score", 1.0
        run.finished_at = utcnow()
    run.heartbeat_at = utcnow()
    run.counts = {
        "candidates": funnel["discovered"], "companies": len(scored),
        "founders": sum(len(s.cd.founders) for s in scored),
        "claims_supported": session.scalar(select(func.count()).select_from(Claim).where(Claim.verified_status == "supported")) or 0,
        "shortlisted": len(report.shortlist),
    }
    session.commit()


def _stream(on_rows: Any, rows: list[tuple[Any, Any, Any]]) -> None:
    try:  # streaming is best effort: a Sheet hiccup never breaks scoring
        on_rows(rows)
    except Exception as exc:
        log.warning("long-list streaming failed: %s", exc)


def perturbed(weights: dict[str, Any], pct: float, rng: np.random.Generator) -> dict[str, Any]:
    """Copy of weights with every SPEC weight/exponent multiplied by U(1-pct, 1+pct)."""
    w = copy.deepcopy(weights)
    for group in (w["track_record"]["weights"], w["interesting"]["weights"], w["final"]["exponents"]):
        for k in group:
            group[k] = float(group[k]) * float(rng.uniform(1 - pct, 1 + pct))
    w["track_record"]["team_depth_bonus"] = float(w["track_record"]["team_depth_bonus"]) * float(rng.uniform(1 - pct, 1 + pct))
    return w
