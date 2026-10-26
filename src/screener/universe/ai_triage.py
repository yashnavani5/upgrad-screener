"""AI triage of queued registry prospects: Gemini-fast reads ONLY the registry row (name, industry code, activity,
registration year, capital, state), 50 rows per call, and labels each as a likely edtech product company, an
education service (school, coaching centre, institute), not education, or unclear.

This is a ROUTING guess, never a fact: it only sets the light-tier queue order (edtech products first) and is
recorded in prospects.triage_reasons as "ai_triage:<label>". Nothing about a company is claimed from it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from screener.models import Prospect, RegistryEntity, Task

log = logging.getLogger(__name__)

Label = Literal["edtech_product", "education_service", "not_education", "unclear"]
PRIORITY_BOOST: dict[str, float] = {"edtech_product": 100.0, "unclear": 20.0, "education_service": 5.0, "not_education": -100.0}

SYSTEM = (
    "You route Indian company-registry rows for an education-startup screener. For each row, judge ONLY from the "
    "registered name and registry fields whether it is likely: 'edtech_product' (a technology-led learning "
    "product, platform, app or software company: names with tech/labs/learning/edu/AI/skills plus a private "
    "limited company), 'education_service' (a school, college, coaching centre, institute, tuition, trust, "
    "society or training provider without a sign of a tech product), 'not_education' (another business), or "
    "'unclear'. This is a routing guess, not a fact: when unsure, answer 'unclear'. Return one item per row id."
)


class Item(BaseModel):
    id: int
    label: Label
    reason: str = Field(description="At most 10 words.")


class Batch(BaseModel):
    items: list[Item]


@dataclass
class TriageReport:
    prospects: int = 0
    calls: int = 0
    labels: dict[str, int] = field(default_factory=dict)
    errors: int = 0


def row_text(i: int, p: Prospect, regs: list[RegistryEntity]) -> str:
    r = regs[0] if regs else None
    year = (r.registered_on.year if r and r.registered_on else None)
    cap = f"{r.paid_up_capital_inr:.0f}" if r and r.paid_up_capital_inr else None
    fields = {"id": i, "name": p.name, "nic": r.nic_code if r else None, "activity": (r.activity or r.sector) if r else None,
              "registered": year, "paid_up_inr": cap, "state": p.state,
              "class": ((r.raw or {}).get("CompanyClass") if r else None)}
    return json.dumps({k: v for k, v in fields.items() if v is not None}, ensure_ascii=False)


def already_triaged(p: Prospect) -> bool:
    return any(str(x).startswith("ai_triage:") for x in (p.triage_reasons or []))


async def triage(factory: Any, gemini: Any, *, batch_size: int = 50, concurrency: int = 12,
                 limit: int | None = None) -> TriageReport:
    report = TriageReport()
    with factory() as s:
        ids = [pid for pid, reasons in s.execute(
            select(Prospect.id, Prospect.triage_reasons).where(Prospect.stage == "queued").order_by(Prospect.triage_score.desc()))
            if not any(str(x).startswith("ai_triage:") for x in (reasons or []))]
    if limit:
        ids = ids[:limit]
    batches = [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]
    sem = asyncio.Semaphore(concurrency)

    async def one(batch_ids: list[Any]) -> None:
        async with sem:  # take a turn BEFORE opening a DB session (thousands of batches, a bounded pool)
            with factory() as s:
                ps = list(s.scalars(select(Prospect).where(Prospect.id.in_(batch_ids))))
                regs = {r.id: r for r in s.scalars(select(RegistryEntity).where(
                    RegistryEntity.id.in_([rid for p in ps for rid in (p.registry_ids or [])])))}
                lines = [row_text(i, p, [regs[u] for r in (p.registry_ids or []) if (u := uuid.UUID(str(r))) in regs])
                         for i, p in enumerate(ps)]
            try:
                prompt = "ROWS (one JSON object per line):\n" + "\n".join(lines)
                out = await gemini.generate_json(prompt, Batch, "fast", system=SYSTEM)
            except Exception as exc:  # fail soft: the batch stays untriaged and keeps its code priority
                report.errors += 1
                log.warning("ai triage batch failed: %s: %s", type(exc).__name__, str(exc)[:300])
                return
            report.calls += 1
            labels = {it.id: it for it in out.items}
            with factory() as s:
                for i, pid in enumerate([p.id for p in ps]):
                    p = s.get(Prospect, pid)
                    it = labels.get(i)
                    label = it.label if it else "unclear"
                    p.triage_reasons = [*(p.triage_reasons or []), f"ai_triage:{label}"]
                    flag_modified(p, "triage_reasons")
                    s.execute(update(Task).where(Task.kind == "light", Task.subject_id == p.id, Task.status == "queued")
                              .values(priority=p.triage_score + PRIORITY_BOOST[label]))
                    report.labels[label] = report.labels.get(label, 0) + 1
                report.prospects += len(ps)
                s.commit()
        if report.calls % 50 == 0:
            log.warning("ai triage: %d prospects, %d calls, %s", report.prospects, report.calls, report.labels)

    await asyncio.gather(*(one(b) for b in batches))
    return report
