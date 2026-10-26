"""Claim drafts: grounding check (code) and idempotent storage."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from screener.models import Claim
from screener.scouts.grounding import normalize, span_in_source


@dataclass
class ClaimDraft:
    entity_type: str            # company | founder | round
    entity_id: uuid.UUID
    field: str
    value: dict[str, Any]
    source_url: str
    source_type: str            # company_site | press | filing | search_snippet | github | other
    evidence_span: str
    retrieved_at: datetime
    extractor_model: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        payload = json.dumps([self.entity_type, str(self.entity_id), self.field, self.value, self.source_url],
                             sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()


def grounding_score(span: str | None, source_text: str) -> float:
    s, src = normalize(span or ""), normalize(source_text)
    if not s or not src:
        return 0.0
    return 100.0 if s in src else float(fuzz.partial_ratio(s, src))


def grounded(draft: ClaimDraft, source_text: str, threshold: int = 90) -> bool:
    """Code check: the evidence span must appear in the fetched source text (fuzzy >= threshold)."""
    if not span_in_source(draft.evidence_span, source_text, threshold):
        return False
    draft.meta["grounding_score"] = round(grounding_score(draft.evidence_span, source_text), 1)
    return True


def store(session: Session, drafts: list[ClaimDraft]) -> int:
    """Insert claims; existing (same claim_key) are skipped. Returns rows inserted."""
    if not drafts:
        return 0
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    now = datetime.now(timezone.utc)
    unique = {d.key(): d for d in drafts}
    rows = [
        {
            "id": uuid.uuid4(), "entity_type": d.entity_type, "entity_id": d.entity_id, "field": d.field,
            "value_json": d.value, "source_url": d.source_url, "source_type": d.source_type,
            "retrieved_at": d.retrieved_at, "evidence_span": d.evidence_span[:4000],
            "extractor_model": d.extractor_model, "extract_confidence": None, "verified_status": "unverified",
            "verifier_confidence": None, "claim_key": k, "meta": d.meta, "created_at": now, "updated_at": now,
        }
        for k, d in unique.items()
    ]
    table = Claim.__table__
    stmt = insert(table).values(rows).on_conflict_do_nothing(index_elements=["claim_key"]).returning(table.c.id)
    return len(session.execute(stmt).all())
