"""Run state machine, budget guard and incremental/resume target selection."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from screener.budget import BudgetGuard
from screener.config import load_yaml
from screener.models import Base, Company, Run
from screener.orchestrator import STAGES, Pipeline, StageResult, final_status, should_run

NOW = datetime.now(timezone.utc)


# --- checkpoints -------------------------------------------------------------------------------------


def test_should_run() -> None:
    assert should_run(None)
    assert not should_run({"status": "done"})
    assert should_run({"status": "partial", "retry": True})
    assert should_run({"status": "failed", "retry": True})
    assert should_run({"status": "running"})                      # interrupted mid-stage
    assert should_run({"status": "skipped", "retry": True})       # skipped by the budget guard / missing creds
    assert not should_run({"status": "skipped", "retry": False})  # skipped by choice (--no-publish)


def test_final_status() -> None:
    done = {s: {"status": "done"} for s in STAGES}
    assert final_status(done) == "succeeded"
    assert final_status({**done, "publish": {"status": "skipped", "retry": False}}) == "succeeded"
    assert final_status({**done, "enrich": {"status": "partial", "retry": True}}) == "partial"
    assert final_status({**done, "score": {"status": "failed"}}) == "failed"


# --- budget guard ------------------------------------------------------------------------------------


def guard(used=0, cap=100, daily_cap=None, daily_used=0, reserve=10) -> BudgetGuard:
    est = load_yaml("providers")["budget"]["estimates"]
    return BudgetGuard(cap, lambda: used, daily_cap, (lambda: daily_used), reserve, est)


def test_budget_sizes_stages_before_the_limit() -> None:
    g = guard(used=20, cap=100, reserve=10)                # 80 left, 70 after reserve, 8 per company
    assert g.remaining() == 80
    assert g.affordable(50, "enrich_per_company") == 8      # floor(70 / 8)
    assert g.affordable(5, "enrich_per_company") == 5
    assert g.fits(4, "pairwise_per_item")                   # 80 >= 4 * 20
    assert not g.fits(5, "pairwise_per_item")


def test_daily_cap_is_respected_across_processes() -> None:
    g = guard(used=0, cap=5000, daily_cap=1000, daily_used=990, reserve=0)
    assert g.remaining() == 10 and g.affordable(100, "verify_per_source") == 9


def test_exhausted() -> None:
    assert guard(used=100, cap=100).exhausted()
    assert not guard(used=99, cap=100).exhausted()


# --- resume / incremental selection ------------------------------------------------------------------


class _Cfg(SimpleNamespace):
    def snapshot(self):
        return {}


def make_pipeline(factory) -> Pipeline:
    cfg = _Cfg(enrich={"incremental": {"enrich_stale_days": 30}, "priority": load_yaml("enrich")["priority"]},
               providers={"budget": {"estimates": {}}}, settings=SimpleNamespace(run_max_llm_calls=100, user_agent="test"))
    return Pipeline(cfg, factory, gemini=None, quota=None)


def test_enrich_targets_skip_finished_and_fresh_companies(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        run = Run(id=uuid.uuid4(), trigger="cli", status="running", started_at=NOW - timedelta(hours=1))
        done_here = Company(id=uuid.uuid4(), name="Done In This Run", enriched_at=NOW - timedelta(minutes=10))
        fresh = Company(id=uuid.uuid4(), name="Fresh", enriched_at=NOW - timedelta(days=3))
        stale = Company(id=uuid.uuid4(), name="Stale", enriched_at=NOW - timedelta(days=90))
        never = Company(id=uuid.uuid4(), name="Never")
        s.add_all([run, done_here, fresh, stale, never])
        s.commit()
        p = make_pipeline(factory)
        monkeypatch.setattr(p, "_ranked", lambda s, top: [done_here, fresh, stale, never])
        todo, here, fresh_n = p.enrich_targets(s, run, 10, incremental=True)
        assert [c.name for c in todo] == ["Stale", "Never"] and (here, fresh_n) == (1, 1)
        todo_full, here_full, _ = p.enrich_targets(s, run, 10, incremental=False)
        assert [c.name for c in todo_full] == ["Fresh", "Stale", "Never"] and here_full == 1  # --full still never redoes this run's work


def test_resume_skips_done_stages_and_retries_the_rest(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    run_id = uuid.uuid4()
    with factory() as s:
        s.add(Run(id=run_id, trigger="cli", status="running", started_at=NOW, options={"publish": False},
                  checkpoints={"discover": {"status": "done"}, "resolve": {"status": "done"},
                               "enrich": {"status": "running"}}))  # killed mid-enrichment
        s.commit()
    p = make_pipeline(factory)
    monkeypatch.setattr("screener.policy.effective_config", lambda s, cfg: cfg)  # the stub cfg has no weights
    calls: list[str] = []

    for stage in STAGES:
        async def fake(s, run, ctx, _stage=stage):
            calls.append(_stage)
            return StageResult("skipped", "--no-publish") if _stage == "publish" else StageResult("done", "ok")
        monkeypatch.setattr(p, f"_{stage}", fake)

    class NullFetcher:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...

    monkeypatch.setattr("screener.http.Fetcher", NullFetcher)
    monkeypatch.setattr("screener.search.SearchRouter.from_config", classmethod(lambda cls, *a, **k: None))
    report = asyncio.run(p.run(resume=run_id))
    assert calls == ["enrich", "verify", "score", "explain", "publish"]   # discover/resolve not redone
    assert report.resumed_from == "enrich" and report.status == "succeeded"
