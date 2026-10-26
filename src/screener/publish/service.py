"""`screener publish`: latest run -> Google Sheet (Shortlist, Left out, Long list, Claims, Run log)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from screener.explain.service import latest_run, left_out_lines
from screener.models import (
    Candidate, Claim, Company, CompanyFounder, Exclusion, Feedback, Founder, Run, Score,
)
from screener.explain.writer import fit_name
from screener.publish.sheets import TABS, SheetPublisher
from screener.verify.verifier import statement

def run_labels(run: Run) -> dict[str, str]:
    """Fit-category labels the run was scored with (AI Policy categories in its config snapshot)."""
    cats = (((run.config_snapshot or {}).get("weights") or {}).get("fit") or {}).get("categories") or []
    return {c["id"]: c["label"] for c in cats}


@dataclass
class PublishReport:
    run_id: Any
    url: str | None
    counts: dict[str, int] = field(default_factory=dict)
    feedback_synced: int = 0


def _founders(session: Session, company_id: Any) -> list[Founder]:
    return list(session.scalars(select(Founder).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
                                .where(CompanyFounder.company_id == company_id).order_by(Founder.full_name)))


def _prev_ranks(session: Session, run: Run, shortlist: bool) -> dict[str, int]:
    prev = session.scalar(select(Run).where(Run.status.in_(["succeeded", "partial"]), Run.started_at < run.started_at)
                          .order_by(Run.started_at.desc()))
    if prev is None:
        return {}
    col = Score.shortlist_rank if shortlist else Score.rank
    rows = session.execute(select(Candidate.company_id, col).join(Score, Score.candidate_id == Candidate.id)
                           .where(Score.run_id == prev.id, col.is_not(None))).all()
    return {str(cid): r for cid, r in rows}


def long_list_records(session: Session, run: Run, limit: int = 200) -> list[dict[str, Any]]:
    prev = _prev_ranks(session, run, shortlist=False)
    rows = session.execute(
        select(Score, Company, Candidate).join(Candidate, Candidate.id == Score.candidate_id)
        .join(Company, Company.id == Candidate.company_id).where(Score.run_id == run.id).order_by(Score.rank).limit(limit)
    ).all()
    reasons = dict(session.execute(select(Exclusion.candidate_id, Exclusion.reason_code).where(Exclusion.run_id == run.id)).all())
    labels = run_labels(run)
    return [long_list_record(s, c, cand, _founders(session, c.id), reasons.get(cand.id), prev.get(str(c.id)), labels)
            for s, c, cand in rows]


def long_list_record(s: Score, c: Company, cand: Candidate, founders: list[Founder], reason: str | None,
                     prev_rank: int | None, labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"Rank": s.rank, "Company": c.name, "Founder(s)": ", ".join(f.full_name for f in founders), "Website": c.website,
            "Fit type": fit_name(s.fit_type or "", labels), "Final": s.final_prime, "T": s.t, "I": s.i, "F": s.f,
            "M": s.m, "N": s.n, "Confidence": s.c, "Gate": cand.gate_status, "Reason code": reason,
            "entity_id": str(c.id), "prev_rank": prev_rank}


def shortlist_records(session: Session, run: Run) -> list[dict[str, Any]]:
    prev = _prev_ranks(session, run, shortlist=True)
    labels = run_labels(run)
    rows = session.execute(
        select(Score, Company).join(Candidate, Candidate.id == Score.candidate_id).join(Company, Company.id == Candidate.company_id)
        .where(Score.run_id == run.id, Score.shortlist_rank.is_not(None)).order_by(Score.shortlist_rank)
    ).all()
    out = []
    for s, c in rows:
        founders = _founders(session, c.id)
        ids = [c.id] + [f.id for f in founders]
        last = session.scalar(select(func.max(Claim.retrieved_at)).where(Claim.entity_id.in_(ids), Claim.verified_status == "supported"))
        ev = (s.evidence or []) + [{}] * 3
        lines = (s.features or {}).get("founder_lines") or {}
        by_id = {str(f.id): f.full_name for f in founders}
        per_founder = [(by_id.get(fid, ""), fl) for fid, fl in lines.items() if fid in by_id]

        def founder_col(key: str) -> str | None:
            vals = [str(fl.get(key) or "").strip() for _, fl in per_founder if fl.get(key)]
            return "\n".join(vals) or None
        out.append({
            "Rank": s.shortlist_rank, "Founder(s)": ", ".join(f.full_name for f in founders), "Company": c.name,
            "Website": c.website, "Fit type": fit_name(s.fit_type or "", labels), "Final": s.final_prime,
            "T": s.t, "I": s.i, "F": s.f, "Confidence": s.c, "Why they made the cut": s.why_line,
            "How they fit upGrad": s.fit_line, "Founders: why they made the cut": founder_col("why"),
            "Founders: how they fit upGrad": founder_col("fit"), "Evidence 1": ev[0].get("url"), "Evidence 2": ev[1].get("url"),
            "Evidence 3": ev[2].get("url"), "LinkedIn URL": ", ".join(f.linkedin_url for f in founders if f.linkedin_url),
            "Last verified": last.date().isoformat() if last else None, "entity_id": str(c.id),
            "prev_rank": prev.get(str(c.id)),
        })
        # The reviewer's label/note from the database (Sheet input synced earlier, or config/reviewer_picks.yaml);
        # rewrite() keeps whatever is typed in the Sheet on top of these.
        fb = session.scalar(select(Feedback).where(Feedback.entity_id == c.id).order_by(Feedback.synced_at.desc()))
        if fb is not None:
            out[-1].update({k: v for k, v in (("Reviewer label", fb.label), ("Reviewer note", fb.note)) if v})
    return out


def left_out_records(session: Session, run: Run) -> list[dict[str, Any]]:
    """The run's curated picks first (with their cited line), then every other exclusion by would-have-ranked."""
    out = []
    lines = sorted(left_out_lines(session, run.id), key=lambda x: (x.get("featured") is None, x.get("featured") or 0))
    for line in lines:
        founders = _founders(session, uuid.UUID(line["entity_id"]))
        out.append({"Company": line["company"], "Founder(s)": ", ".join(f.full_name for f in founders),
                    "Reason code": line["reason_code"], "Explanation": line.get("featured_line") or line["explanation"],
                    "Would have ranked": line["would_have_ranked"], "Source": line["source"], "entity_id": line["entity_id"]})
    return out


def claims_records(session: Session, run: Run) -> list[dict[str, Any]]:
    """Every claim behind shortlisted and left-out entities (SPEC 4.3)."""
    companies = session.execute(select(Company).join(Candidate, Candidate.company_id == Company.id)
                                .where(Candidate.run_id == run.id)).scalars().all()
    out = []
    for c in companies:
        founders = _founders(session, c.id)
        subjects = {c.id: c.name} | {f.id: f.full_name for f in founders}
        for cl in session.scalars(select(Claim).where(Claim.entity_id.in_(list(subjects))).order_by(Claim.field)):
            corr = (cl.meta or {}).get("corroboration") or {}
            out.append({"Company": c.name, "Subject": subjects[cl.entity_id], "Field": cl.field,
                        "Value": statement(cl.field, cl.value_json or {}, subjects[cl.entity_id]),
                        "Status": cl.verified_status, "Verifier confidence": cl.verifier_confidence,
                        "Corroborated": "" if not corr.get("needed") else ("yes" if corr.get("corroborated") else "no"),
                        "Source URL": cl.source_url, "Evidence span": (cl.evidence_span or "")[:500],
                        "Retrieved at": cl.retrieved_at.isoformat() if cl.retrieved_at else None, "claim_id": str(cl.id)})
    return out


def run_log_record(session: Session, run: Run) -> dict[str, Any]:
    counts = run.counts or {}
    usage = run.provider_usage or {}
    quota = "; ".join(f"{k}: {v.get('calls', v)}" if isinstance(v, dict) else f"{k}: {v}" for k, v in usage.items())
    return {"run_id": str(run.id), "Started": run.started_at.isoformat() if run.started_at else None,
            "Finished": run.finished_at.isoformat() if run.finished_at else None, "Status": run.status,
            "Candidates": counts.get("candidates"), "Companies": counts.get("companies"),
            "Verified claims": counts.get("claims_supported"), "Shortlisted": counts.get("shortlisted"),
            "Provider quota used": quota, "Cost (USD)": run.cost_usd}


SHEET_REVIEWER = "upGrad reviewer (Google Sheet)"


def sync_feedback(session: Session, pub: SheetPublisher) -> int:
    """Reviewer label/note from the Sheet -> feedback table (the Sheet stays the reviewer's surface)."""
    n = 0
    for entity_id, vals in pub.reviewer_values("Shortlist").items():
        try:
            eid = uuid.UUID(entity_id)
        except ValueError:
            continue
        fb = session.scalar(select(Feedback).where(Feedback.entity_id == eid))
        label, note = vals.get("Reviewer label"), vals.get("Reviewer note")
        if fb is None:
            session.add(Feedback(id=uuid.uuid4(), entity_id=eid, reviewer=SHEET_REVIEWER, label=label, note=note,
                                 synced_at=datetime.now(timezone.utc)))
            n += 1
        elif (fb.label, fb.note) != (label, note):  # edited in the Sheet: now the Sheet reviewer's entry
            fb.label, fb.note, fb.reviewer, fb.synced_at = label, note, SHEET_REVIEWER, datetime.now(timezone.utc)
            n += 1
    session.commit()
    return n


def publish(session: Session, pub: SheetPublisher, run: Run | None = None) -> PublishReport:
    run = run or latest_run(session)
    if run is None:
        raise RuntimeError("no scored run to publish")
    report = PublishReport(run.id, pub.url)
    report.feedback_synced = sync_feedback(session, pub)
    report.counts["Long list"] = sum(pub.upsert("Long list", long_list_records(session, run)).values())
    report.counts["Shortlist"] = pub.rewrite("Shortlist", shortlist_records(session, run))
    report.counts["Left out"] = pub.rewrite("Left out", left_out_records(session, run))
    report.counts["Claims"] = sum(pub.upsert("Claims", claims_records(session, run)).values())
    report.counts["Run log"] = sum(pub.upsert("Run log", [run_log_record(session, run)]).values())
    for name in TABS:
        pub.style(name)
    pub.tidy()
    run.sheet_url = pub.url
    session.commit()
    return report
