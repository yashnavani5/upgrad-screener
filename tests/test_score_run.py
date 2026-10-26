"""Smoke test: a whole `score()` run on SQLite (no LLM, stub embedder) persists scores under the AI Policy."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from screener.models import Base, Claim, Company, Run, Score
from screener.policy import apply, default_policy
from screener.score.service import score

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class StubEmbedder:
    async def embed(self, texts, task=None):
        return SimpleNamespace(vectors=[[float(len(t) % 7), float(i), 1.0] for i, t in enumerate(texts)])


def _company(s: Session, name: str, caps: list[str], segs: list[str]) -> Company:
    c = Company(id=uuid.uuid4(), name=name, domain=f"{name.lower()}.example", status="active", segment_tags=segs,
                capability_tags=caps, lookups={}, verified_at=NOW, description=f"{name} builds AI tutoring for students in India.")
    s.add(c)
    s.add(Claim(id=uuid.uuid4(), entity_type="company", entity_id=c.id, field="description",
                value_json={"text": c.description}, source_url=f"https://{c.domain}/", source_type="company_site",
                retrieved_at=NOW, evidence_span=c.description, verified_status="supported", meta={}))
    return c


def test_score_run_persists_every_category(cfg) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    p = default_policy(cfg)
    extra = p.categories[0].model_copy(deep=True)
    extra.id, extra.label = "skilling", "Skilling partner"
    p.categories.append(extra)
    pcfg = apply(cfg, p, 1)
    with Session(engine, expire_on_commit=False) as s:
        for i, name in enumerate(["Alpha", "Beta", "Gamma", "Delta"]):
            _company(s, name, ["ai_tutoring"] if i % 2 == 0 else [], ["test_prep"])
        s.commit()
        report = asyncio.run(score(s, pcfg, embedder=StubEmbedder(), gemini=None, pairwise=False))
        run = s.get(Run, report.run_id)
        assert run.status in ("succeeded", "partial"), run.error_summary
        rows = list(s.scalars(select(Score).where(Score.run_id == run.id)))
        assert len(rows) == 4
        names = {b["name"] for b in rows[0].breakdown}
        assert {"F_acquire", "F_partner", "F_co_build", "F_skilling"} <= names
