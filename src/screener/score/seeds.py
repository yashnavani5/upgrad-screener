"""Calibration seeds (SPEC 3.11), refreshed from verified data by an explicit rule.

The rule never reads scores or weights, so recall@50 stays meaningful when weights change:
  positive = India + passes every hard gate + not inactive + at least one upGrad gap-map tag
             + external validation (a corroborated round in the momentum window, or a listed top investor)
  negative = a hard exclusion backed by a claim (competitor at scale, upGrad-owned, too large, not education)
Entries written by a reviewer (origin != "auto") are never changed or removed; auto entries are replaced each refresh,
and a company a reviewer listed on either side is never auto-added to the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from screener.resolve.normalize import normalize_name

HARD = ("UPGRAD_OWNED", "COMPETITOR", "ACQUIRED", "TOO_LARGE", "NOT_EDUCATION", "INACTIVE", "UNVERIFIABLE")
NEGATIVE = ("COMPETITOR", "UPGRAD_OWNED", "TOO_LARGE", "NOT_EDUCATION")
LISTED = ("COMPETITOR", "UPGRAD_OWNED")  # decided by config lists, so no claim URL exists
ORIGIN = "auto"


@dataclass(frozen=True)
class SeedRow:
    company: str
    website: str | None
    rank: int | None
    reason_code: str | None          # hard or soft exclusion code of the run, None if shortlisted
    reason_text: str | None
    reason_url: str | None           # source of the exclusion claim
    geo: str
    tags: tuple[str, ...]             # gap-map capabilities + segments (supported claims)
    recent_rounds: int                # corroborated rounds inside the momentum window
    top_investor: bool
    evidence_url: str | None          # strongest evidence URL of the company


def positive_reason(r: SeedRow) -> str | None:
    if r.geo != "india" or (r.reason_code in HARD) or not r.tags:
        return None
    if r.recent_rounds < 1 and not r.top_investor:
        return None
    val = f"{r.recent_rounds} corroborated round(s) in the window" if r.recent_rounds else "listed top investor"
    return f"India, passes all hard gates, gap-map fit ({', '.join(sorted(r.tags))}), {val}"


def pick(rows: list[SeedRow], max_positives: int, max_negatives: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Auto seeds from one scored run. Negatives favour the highest-ranked (most informative) ones."""
    pos, neg = [], []
    for r in rows:
        why = positive_reason(r)
        if why and r.evidence_url:
            pos.append((r.company.lower(), {"company": r.company, "website": r.website, "source_url": r.evidence_url,
                                            "reason": why, "origin": ORIGIN}))
        elif r.reason_code in NEGATIVE and (r.reason_url or (r.reason_code in LISTED and r.website)):
            # Competitor / upGrad-owned come from config/exclusions.yaml lists, not claims: the company's own site
            # identifies the entity.
            neg.append((r.rank if r.rank is not None else 10**9, {
                "company": r.company, "website": r.website, "source_url": r.reason_url or r.website,
                "reason": f"{r.reason_code}: {r.reason_text}", "origin": ORIGIN}))
    pos.sort(key=lambda x: x[0])
    neg.sort(key=lambda x: x[0])
    return [p for _, p in pos[:max_positives]], [n for _, n in neg[:max_negatives]]


def merge(existing: dict[str, Any], auto_pos: list[dict[str, Any]], auto_neg: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Keep reviewer entries untouched; replace auto entries; never put one company on both sides."""
    def manual(side: str) -> list[Any]:
        return [e for e in (existing.get(side) or []) if not (isinstance(e, dict) and e.get("origin") == ORIGIN)]

    def key(e: Any) -> str:
        name = (e.get("company") or e.get("name") or "") if isinstance(e, dict) else str(e)
        return normalize_name(name)

    man_pos, man_neg = manual("positives"), manual("negatives")
    taken = {key(e) for e in man_pos + man_neg}
    out_pos, out_neg = list(man_pos), list(man_neg)
    for side, autos in ((out_pos, auto_pos), (out_neg, auto_neg)):
        for e in autos:
            k = key(e)
            if k and k not in taken:
                side.append(e)
                taken.add(k)
    return {"positives": out_pos, "negatives": out_neg}


def scored_run_id(session: Any) -> Any:
    """Newest finished run that has scores (a run whose only gap is a skipped publish is still a scored run)."""
    from sqlalchemy import exists, select

    from screener.models import Run, Score

    return session.scalar(select(Run.id).where(Run.status.in_(["succeeded", "partial"]),
                                               exists().where(Score.run_id == Run.id)).order_by(Run.started_at.desc()))


def load_rows(session: Any, run_id: Any) -> list[SeedRow]:
    """One SeedRow per scored company of `run_id` (features, exclusion and its source claim)."""
    from sqlalchemy import select

    from screener.models import Candidate, Claim, Company, Exclusion, Score

    excl = {e.candidate_id: e for e in session.scalars(select(Exclusion).where(Exclusion.run_id == run_id))}
    claim_ids = [e.source_claim_id for e in excl.values() if e.source_claim_id]
    urls = dict(session.execute(select(Claim.id, Claim.source_url).where(Claim.id.in_(claim_ids))).all()) if claim_ids else {}
    rows = []
    for sc, cand, co in session.execute(select(Score, Candidate, Company).join(Candidate, Candidate.id == Score.candidate_id)
                                        .join(Company, Company.id == Candidate.company_id).where(Score.run_id == run_id)):
        f = sc.features or {}
        e = excl.get(cand.id)
        velocity = (f.get("momentum") or {}).get("funding_velocity") or [0]
        rows.append(SeedRow(
            company=co.name, website=co.website, rank=sc.rank,
            reason_code=e.reason_code if e else None, reason_text=e.explanation if e else None,
            reason_url=urls.get(e.source_claim_id) if e and e.source_claim_id else None,
            geo=str(f.get("geo") or "unknown"),
            tags=tuple(sorted(set(f.get("capabilities") or []) | set(f.get("segments") or []))),
            recent_rounds=int(velocity[0] or 0),
            top_investor=any((fd or {}).get("top_investor") for fd in f.get("founders") or []),
            evidence_url=next((ev.get("url") for ev in sc.evidence or [] if ev.get("url")), None),
        ))
    return rows


def refresh(session: Any, path: Path, existing: dict[str, Any], conf: dict[str, Any], run_id: Any = None,
            write: bool = True) -> dict[str, Any]:
    """Recompute auto seeds from a scored run and merge them into `path`. Returns counts for logging."""
    run_id = run_id or scored_run_id(session)
    if run_id is None:
        return {"run_id": None, "positives": 0, "negatives": 0, "manual": 0}
    pos, neg = pick(load_rows(session, run_id), int(conf.get("max_positives", 40)), int(conf.get("max_negatives", 30)))
    merged = merge(existing, pos, neg)
    if write:
        save(path, merged)
    manual = sum(1 for side in merged.values() for e in side if not (isinstance(e, dict) and e.get("origin") == ORIGIN))
    return {"run_id": str(run_id), "positives": len(pos), "negatives": len(neg), "manual": manual, "seeds": merged}


def save(path: Path, seeds: dict[str, list[Any]]) -> None:
    """Rewrite the positives/negatives lists, keeping the file's header comments."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 140
    data = yaml.load(path.read_text(encoding="utf-8")) or CommentedMap()
    for side in ("positives", "negatives"):
        seq = CommentedSeq()
        for e in seeds[side]:
            seq.append(CommentedMap(e) if isinstance(e, dict) else e)
        data[side] = seq
    with path.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
