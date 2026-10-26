"""Reviewer picks (`config/reviewer_picks.yaml`): companies the upGrad team asked the pipeline to consider.

A pick enters the pipeline as discoveries (scout "reviewer", the file's `origin` in `extra`) built only from its
name, founders, website and source article URLs; resolve, enrich and verify then treat it like any other
candidate, so every fact on its card is a verified claim. The pick's category and note become the reviewer
label/note in `feedback`: the reviewer's view, shown as such and never as evidence. A label or note a reviewer
typed in the Sheet is never overwritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.config import CONFIG_DIR
from screener.scouts.base import DiscoveryItem

SCOUT = "reviewer"
PICKS_FILE = CONFIG_DIR / "reviewer_picks.yaml"
CATEGORY_LABEL = {"acquire": "Acquire", "partner": "Partner", "co_build": "Co-build"}


class Pick(BaseModel):
    company: str = Field(min_length=1)
    website: HttpUrl | None = None
    founders: list[str] = Field(default_factory=list)
    category: str
    sources: list[HttpUrl] = Field(min_length=1)
    note: str = ""


class Picks(BaseModel):
    origin: str = Field(min_length=1)
    picks: list[Pick] = Field(default_factory=list)


def load_picks(path: Path = PICKS_FILE) -> Picks:
    return Picks.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def label_for(pick: Pick) -> str:
    return f"Reviewer pick: {CATEGORY_LABEL.get(pick.category, pick.category)}"


def discovery_items(picks: Picks) -> list[DiscoveryItem]:
    """One discovery per (source article, founder); a pick without founders gets one per source. Pure."""
    items = []
    for p in picks.picks:
        extra = {"origin": picks.origin, "pick": p.company, "suggested_category": p.category}
        for src in p.sources:
            for person in p.founders or [None]:
                items.append(DiscoveryItem(scout=SCOUT, raw_company_name=p.company, raw_person_name=person,
                                           company_url=str(p.website) if p.website else None, source_url=str(src),
                                           extra=extra))
    return items


@dataclass
class ApplyReport:
    linked: dict[str, uuid.UUID] = field(default_factory=dict)   # pick -> company id
    unresolved: list[str] = field(default_factory=list)
    written: int = 0
    kept_sheet: list[str] = field(default_factory=list)          # a reviewer's own Sheet entry was kept


def pick_companies(session: Session, picks: Picks) -> dict[str, uuid.UUID]:
    """pick name -> company id, from this origin's resolved reviewer discoveries."""
    from screener.models import Discovery

    out: dict[str, uuid.UUID] = {}
    for d in session.scalars(select(Discovery).where(Discovery.scout == SCOUT, Discovery.company_id.is_not(None))):
        if (d.extra or {}).get("origin") == picks.origin and (name := (d.extra or {}).get("pick")):
            out.setdefault(name, d.company_id)
    return out


def apply_feedback(session: Session, picks: Picks) -> ApplyReport:
    """Write each linked pick's label/note to `feedback` (reviewer = origin). Entries typed in the Sheet win."""
    from screener.models import Feedback

    report = ApplyReport(linked=pick_companies(session, picks))
    now = datetime.now(timezone.utc)
    for p in picks.picks:
        cid = report.linked.get(p.company)
        if cid is None:
            report.unresolved.append(p.company)
            continue
        fb = session.scalar(select(Feedback).where(Feedback.entity_id == cid).order_by(Feedback.synced_at.desc()))
        label, note = label_for(p), " ".join(p.note.split()) or None
        if fb is None:
            session.add(Feedback(id=uuid.uuid4(), entity_id=cid, reviewer=picks.origin, label=label, note=note, synced_at=now))
            report.written += 1
        elif fb.reviewer != picks.origin and (fb.label or fb.note):
            report.kept_sheet.append(p.company)
        elif (fb.label, fb.note, fb.reviewer) != (label, note, picks.origin):
            fb.label, fb.note, fb.reviewer, fb.synced_at = label, note, picks.origin, now
            report.written += 1
    session.flush()
    return report


def must_consider_names(picks: Picks) -> list[str]:
    return [p.company for p in picks.picks]


def maybe_apply(session: Session) -> ApplyReport | None:
    """Re-link picks after resolve (companies can merge); silent when there is no picks file."""
    if not PICKS_FILE.exists():
        return None
    picks = load_picks()
    return apply_feedback(session, picks) if picks.picks else None


def as_dict(report: ApplyReport) -> dict[str, Any]:
    return {"linked": len(report.linked), "unresolved": report.unresolved, "written": report.written,
            "kept_sheet": report.kept_sheet}
