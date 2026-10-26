"""`screener explain`: write why/fit lines + evidence for the shortlist of a run, and left-out lines."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.explain.people import Candidate as CuratedCandidate
from screener.explain.people import curated_line, founder_lines, pick_curated
from screener.explain.writer import fit_labels, pick_evidence, write
from screener.policy import category_labels
from screener.models import Candidate, Claim, Company, CompanyFounder, Exclusion, Founder, Run, Score
from screener.score.features import usable


@dataclass
class ExplainReport:
    run_id: Any
    written: int = 0
    llm: int = 0
    template: int = 0
    regenerated: int = 0
    founder_lines: int = 0
    curated: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)


def latest_run(session: Session) -> Run | None:
    return session.scalar(select(Run).where(Run.status.in_(["succeeded", "partial"])).order_by(Run.started_at.desc()))


async def explain_run(session: Session, cfg: Any, gemini: Any, run: Run | None = None) -> ExplainReport:
    run = run or latest_run(session)
    if run is None:
        raise RuntimeError("no scored run yet: run `screener score` first")
    report = ExplainReport(run.id)
    rows = session.execute(
        select(Score, Company).join(Candidate, Candidate.id == Score.candidate_id).join(Company, Company.id == Candidate.company_id)
        .where(Score.run_id == run.id, Score.shortlist_rank.is_not(None)).order_by(Score.shortlist_rank)
    ).all()
    sem = asyncio.Semaphore(4)
    vconf = cfg.enrich["verification"]

    async def one(score: Score, company: Company) -> None:
        founders = session.scalars(select(Founder).join(CompanyFounder, CompanyFounder.founder_id == Founder.id)
                                   .where(CompanyFounder.company_id == company.id)).all()
        subjects = {company.id: company.name} | {f.id: f.full_name for f in founders}
        claims = [c for c in session.scalars(select(Claim).where(Claim.entity_id.in_(list(subjects)))) if usable(c)]
        claims = [c for c in claims if c.field not in ("website",)]
        async with sem:
            w = await write(gemini, company.name, score.fit_type or "acquire", claims, subjects, cfg.gap_map,
                            category_labels(cfg))
        score.why_line, score.fit_line = w.why_line, w.fit_line
        score.evidence = pick_evidence(claims, set(w.why_ids) | set(w.fit_ids), company.domain, vconf)
        # One line per founder (top 2 by verified background), from that founder's verified claims only.
        ranked = sorted(founders, key=lambda f: -sum(1 for c in claims if c.entity_id == f.id))[:2]
        labels = fit_labels([c for c in claims if c.entity_type == "company"], cfg.gap_map)
        lines = {}
        for f in ranked:
            async with sem:
                fl = await founder_lines(gemini, f.full_name, company.name, [c for c in claims if c.entity_id == f.id], labels)
            lines[str(f.id)] = {"line": fl.line, "why": fl.why, "fit": fl.fit, "claim_ids": fl.claim_ids, "method": fl.method}
            report.founder_lines += 1
        score.features = {**(score.features or {}), "founder_lines": lines, "explanation": {
            "why_claim_ids": w.why_ids, "fit_claim_ids": w.fit_ids, "method": w.method, "attempts": w.attempts,
            "errors": w.errors}}
        report.written += 1
        report.llm += w.method == "llm"
        report.template += w.method == "template"
        report.regenerated += w.method == "llm" and w.attempts > 1
        report.rows.append({"rank": score.shortlist_rank, "company": company.name, "fit_type": score.fit_type,
                            "why": w.why_line, "fit": w.fit_line, "method": w.method, "evidence": score.evidence})

    await asyncio.gather(*(one(s, c) for s, c in rows))
    session.commit()
    report.curated = await curate_left_out(session, run, gemini)
    session.commit()
    report.rows.sort(key=lambda r: r["rank"])
    return report


async def curate_left_out(session: Session, run: Run, gemini: Any) -> list[str]:
    """Mark up to 3 exclusions as the curated "left out" picks (one per category) with a cited line."""
    rows = session.execute(
        select(Exclusion, Company, Score).join(Candidate, Candidate.id == Exclusion.candidate_id)
        .join(Company, Company.id == Candidate.company_id).join(Score, Score.candidate_id == Candidate.id)
        .where(Exclusion.run_id == run.id)).all()
    for ex, _, _ in rows:
        ex.featured = ex.featured_category = ex.featured_line = None
    by_key = {ex.id: (ex, company) for ex, company, _ in rows}
    picks = pick_curated([CuratedCandidate(ex.id, ex.reason_code, ex.would_have_ranked, (score.features or {}).get("geo", "unknown"))
                          for ex, _, score in rows])
    out = []
    for i, (category, cand) in enumerate(picks, start=1):
        ex, company = by_key[cand.key]
        claims = [c for c in session.scalars(select(Claim).where(Claim.entity_id == company.id)) if usable(c)]
        w = await curated_line(gemini, company.name, ex.reason_code, ex.explanation, claims)
        ex.featured, ex.featured_category, ex.featured_line = i, category, w.line
        out.append(f"{company.name} ({category}, {ex.reason_code})")
    return out


def left_out_lines(session: Session, run_id: Any) -> list[dict[str, Any]]:
    """name, reason code, one-sentence explanation, would-have-ranked, source."""
    rows = session.execute(
        select(Exclusion, Company, Score).join(Candidate, Candidate.id == Exclusion.candidate_id)
        .join(Company, Company.id == Candidate.company_id).join(Score, Score.candidate_id == Candidate.id)
        .where(Exclusion.run_id == run_id).order_by(Exclusion.would_have_ranked)
    ).all()
    out = []
    for ex, company, score in rows:
        source = session.scalar(select(Claim.source_url).where(Claim.id == ex.source_claim_id)) if ex.source_claim_id else None
        source = source or next((e.get("url") for e in score.evidence or []), None) or company.website
        sentence = ex.explanation.split(". ")[0].rstrip(".") + "."
        out.append({"entity_id": str(company.id), "company": company.name, "reason_code": ex.reason_code,
                    "explanation": sentence, "would_have_ranked": ex.would_have_ranked, "source": source,
                    "final": score.final_prime, "featured": ex.featured, "featured_line": ex.featured_line})
    return out
