"""`screener audit sample`: a reproducible random sample of supported claims from the latest shortlist, for a
manual accuracy check (open each source, confirm the quote is there and says what the claim says).

Stratified by field so funding, roles and descriptions are all covered; a fixed seed makes the sample repeatable.
The reviewer's verdicts go in the CSV's `verdict` column (correct / wrong / unclear) and `note`.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.models import Candidate, Claim, Company, CompanyFounder, Founder, Score
from screener.score.seeds import scored_run_id
from screener.verify.verifier import statement

FIELDS = ["sample_id", "company", "subject", "field", "statement", "evidence_span", "source_url", "source_type",
          "verifier_confidence", "claim_id", "verdict", "note"]


def sample_claims(session: Session, n: int = 30, seed: int = 7) -> list[dict[str, Any]]:
    run_id = scored_run_id(session)
    companies = session.execute(select(Company).join(Candidate, Candidate.company_id == Company.id)
                                .join(Score, Score.candidate_id == Candidate.id)
                                .where(Score.run_id == run_id, Score.shortlist_rank.is_not(None))).scalars().all()
    subjects: dict[Any, tuple[str, str]] = {c.id: (c.name, c.name) for c in companies}
    for cid, fid, name in session.execute(select(CompanyFounder.company_id, Founder.id, Founder.full_name)
                                          .join(Founder, Founder.id == CompanyFounder.founder_id)
                                          .where(CompanyFounder.company_id.in_([c.id for c in companies]))):
        subjects[fid] = (next(c.name for c in companies if c.id == cid), name)
    claims = session.scalars(select(Claim).where(Claim.entity_id.in_(list(subjects)), Claim.verified_status == "supported")).all()
    by_field: dict[str, list[Claim]] = defaultdict(list)
    for c in sorted(claims, key=lambda c: str(c.id)):
        by_field[c.field].append(c)
    rng = random.Random(seed)
    for pool in by_field.values():
        rng.shuffle(pool)
    picked: list[Claim] = []
    while len(picked) < n and any(by_field.values()):  # round-robin across fields
        for f in sorted(by_field):
            if by_field[f] and len(picked) < n:
                picked.append(by_field[f].pop())
    out = []
    for i, c in enumerate(picked, start=1):
        company, subject = subjects[c.entity_id]
        out.append({"sample_id": i, "company": company, "subject": subject, "field": c.field,
                    "statement": statement(c.field, c.value_json or {}, subject), "evidence_span": c.evidence_span,
                    "source_url": c.source_url, "source_type": c.source_type, "verifier_confidence": c.verifier_confidence,
                    "claim_id": str(c.id), "verdict": "", "note": ""})
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
