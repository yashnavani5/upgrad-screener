"""Reviewer picks: discovery items from names/sources only; label/note to feedback; Sheet entries win."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from screener.models import Base, Company, Discovery, Feedback
from screener.reviewer import PICKS_FILE, Picks, apply_feedback, discovery_items, label_for, load_picks

PICKS = Picks.model_validate({"origin": "Analyst shortlist (test)", "picks": [
    {"company": "Acme Learn", "website": "https://acme.example", "founders": ["A One", "B Two"], "category": "acquire",
     "sources": ["https://news.example/acme-raises", "https://news.example/acme-profile"], "note": "  Buy   it. "},
    {"company": "Solo", "category": "co_build", "sources": ["https://news.example/solo"], "note": ""},
    {"company": "Ghost", "category": "partner", "sources": ["https://news.example/ghost"], "note": "never resolved"},
]})


def test_discovery_items_carry_only_names_and_sources() -> None:
    items = discovery_items(PICKS)
    assert len(items) == 2 * 2 + 1 + 1  # sources x founders; one per source without founders
    acme = [i for i in items if i.raw_company_name == "Acme Learn"]
    assert {i.raw_person_name for i in acme} == {"A One", "B Two"}
    assert all(i.scout == "reviewer" and i.extra["origin"] == PICKS.origin and i.evidence_span is None for i in items)
    assert len({i.dedupe_key() for i in items}) == len(items)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _link(s: Session, pick: str, company_id: uuid.UUID) -> None:
    s.add(Discovery(id=uuid.uuid4(), batch_id=uuid.uuid4(), scout="reviewer", raw_company_name=pick,
                    source_url=f"https://news.example/{pick}", dedupe_key=uuid.uuid4().hex,
                    extra={"origin": PICKS.origin, "pick": pick}, company_id=company_id))


def test_apply_feedback_writes_labels_and_keeps_sheet_entries() -> None:
    with _session() as s:
        acme, solo = Company(id=uuid.uuid4(), name="Acme Learn"), Company(id=uuid.uuid4(), name="Solo")
        s.add_all([acme, solo])
        s.flush()
        _link(s, "Acme Learn", acme.id)
        _link(s, "Solo", solo.id)
        s.add(Feedback(id=uuid.uuid4(), entity_id=solo.id, reviewer="upGrad reviewer (Google Sheet)", label="Hold",
                       note="typed in the Sheet", synced_at=datetime.now(timezone.utc)))
        s.flush()

        r = apply_feedback(s, PICKS)
        assert r.unresolved == ["Ghost"] and r.kept_sheet == ["Solo"] and r.written == 1
        fb = s.scalar(select(Feedback).where(Feedback.entity_id == acme.id))
        assert (fb.label, fb.note, fb.reviewer) == ("Reviewer pick: Acquire", "Buy it.", PICKS.origin)
        assert s.scalar(select(Feedback).where(Feedback.entity_id == solo.id)).note == "typed in the Sheet"
        assert apply_feedback(s, PICKS).written == 0  # idempotent


def test_shipped_picks_file_is_valid() -> None:
    picks = load_picks(PICKS_FILE)
    assert picks.origin and picks.picks
    assert all(p.sources and p.category in {"acquire", "partner", "co_build"} for p in picks.picks)
    assert label_for(picks.picks[0]).startswith("Reviewer pick: ")
