"""Impact preview for an AI Policy draft, with NO LLM calls: every score is recomputed from stored, verified data
under the current and the draft policy; the pairwise judge is not re-run (its stored Bradley-Terry percentiles from
the latest scored run are reused), and description embeddings come from the on-disk cache (CPU fallback otherwise).
Both sides use the same inputs, so the difference is the policy alone."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.models import Company
from screener.policy import Policy, active, apply
from screener.score import core
from screener.score.features import load
from screener.score.fit import categories
from screener.score.gates import GateResult, evaluate, on_list, policy_filter
from screener.score.mmr import MmrItem, select as mmr_select
from screener.score.service import compute, embed_companies, stored_bt_pct
from screener.scouts.prefilter import Prefilter


def _shortlist(session: Session, cfg: Any, companies: list[Company], embeddings: dict[Any, list[float]],
               bt: dict[Any, float], today: Any) -> dict[str, Any]:
    w = cfg.weights
    datas = load(session, cfg, companies, today)
    scored = compute(datas, w, cfg.gap_map, embeddings, today)
    pol = w.get("policy") or {}
    filters = pol.get("filters") or {}
    pf = Prefilter(cfg.sources.get("prefilter_keywords", []))
    min_signals = int(w["shortlist_gate"]["min_verified_signal_claims"])
    blend = w["pairwise"]["blend"]
    for s in scored:
        s.gate = evaluate(s.cd, cfg.exclusions, pf, min_signals, filters)
        s.bt_pct = bt.get(s.key)
        s.final_prime = core.blend(s.final, s.bt_pct, float(blend["final"]), float(blend["bt_pct"]))
    for rank, s in enumerate(sorted(scored, key=lambda s: -s.final_prime), start=1):
        s.rank = rank
    for s in scored:
        if s.gate.passed and (why := policy_filter(s, filters, pol.get("blocklist") or [])):
            s.gate = GateResult(False, False, "POLICY_FILTER", why)
    mm = w["mmr"]
    pool = [s for s in scored if s.gate.shortlist_ok]
    picked = mmr_select([MmrItem(s.key, s.final_prime, s.fit_type, embeddings.get(s.key), s.cd.geo) for s in pool],
                        int(mm["shortlist_max"]), float(mm["lambda"]), float(mm["same_fit_type_sim_bonus"]),
                        int(mm["min_per_fit_type"]), [c["id"] for c in categories(w["fit"])], {"india": int(mm.get("min_india", 0))},
                        pinned={s.key for s in pool if on_list(s.cd, pol.get("must_consider") or [])})
    by_key = {s.key: s for s in scored}
    rows = [{"rank": i, "entity_id": str(it.key), "company": by_key[it.key].cd.company.name, "fit_type": by_key[it.key].fit_type,
             "region": by_key[it.key].cd.geo, "final": round(by_key[it.key].final_prime, 4)} for i, it in enumerate(picked, start=1)]
    reasons = Counter(s.gate.reason_code for s in scored if s.gate.reason_code)
    return {"rows": rows, "scored": len(scored), "eligible": len(pool), "excluded_by_reason": dict(reasons.most_common()),
            "by_category": dict(Counter(r["fit_type"] for r in rows)), "by_region": dict(Counter(r["region"] for r in rows)),
            "policy_filtered": [{"company": s.cd.company.name, "why": s.gate.explanation}
                                for s in sorted(scored, key=lambda s: s.rank or 0) if s.gate.reason_code == "POLICY_FILTER"][:25]}


async def preview(session: Session, cfg: Any, draft: Policy, gemini: Any = None) -> dict[str, Any]:
    """`gemini`: only to read the scoring run's cached Gemini embeddings (same inputs as the published shortlist);
    a cache miss falls back to local CPU embeddings for both sides. No generation call is ever made."""
    from screener.embed import Embedder

    today = datetime.now(timezone.utc).date()
    current_policy, current_version = active(session, cfg)
    current_cfg, draft_cfg = apply(cfg, current_policy, current_version), apply(cfg, draft, None)
    companies = list(session.scalars(select(Company).where(Company.verified_at.is_not(None))))
    bt = stored_bt_pct(session)
    embeddings = await embed_companies(load(session, current_cfg, companies, today), Embedder(cfg, gemini=gemini))
    cur = _shortlist(session, current_cfg, companies, embeddings, bt, today)
    new = _shortlist(session, draft_cfg, companies, embeddings, bt, today)
    cur_rank = {r["entity_id"]: r for r in cur["rows"]}
    new_rank = {r["entity_id"]: r for r in new["rows"]}
    return {
        "current_version": current_version,
        "current": cur, "draft": new,
        "entered": [r for k, r in new_rank.items() if k not in cur_rank],
        "left": [r for k, r in cur_rank.items() if k not in new_rank],
        "moved": [{**r, "from": cur_rank[k]["rank"]} for k, r in new_rank.items() if k in cur_rank and cur_rank[k]["rank"] != r["rank"]],
        "note": "Recomputed from stored verified data with no AI calls; the pairwise judge is reused from the latest run.",
    }
