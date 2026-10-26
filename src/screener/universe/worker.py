"""`screener worker`: real-time processing of the task queue.

light  -> classify a prospect (website + homepage + 1 LLM call); fit -> promote + queue deep
deep   -> enrich one company (claims)                          -> queue verify
verify -> verify that company's claims (fresh fetch + verifier)

Every task commits its result immediately. Several workers can run at once (row locks)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from screener.llm import LLMBudgetExceeded
from screener.models import Company, Prospect, RegistryEntity, Task
from screener.universe.light import BudgetExhausted, already_classified, classify, record
from screener.universe.store import claim, enqueue, fail, finish

log = logging.getLogger(__name__)


@dataclass
class WorkerStats:
    done: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    llm_calls: int = 0

    def bump(self, d: dict[str, int], k: str) -> None:
        d[k] = d.get(k, 0) + 1


class Worker:
    def __init__(self, cfg: Any, factory: sessionmaker[Session], *, fetcher: Any, router: Any, gemini: Any, quota: Any) -> None:
        self.cfg, self.factory, self.fetcher, self.router, self.gemini, self.quota = cfg, factory, fetcher, router, gemini, quota
        self.wconf = cfg.universe["worker"]
        self.lconf = cfg.universe["light"]
        self.stats = WorkerStats()
        self._policy_cfg, self._policy_at = cfg, 0.0
        rpm = float(self.lconf["search_rpm"])
        for provider in ("searxng", "ddgs"):  # gentle on free search engines
            fetcher.limiter.configure(f"provider:{provider}", rpm)

    def policy_cfg(self) -> Any:
        """Config overlaid by the active AI Policy, re-read every minute so a saved policy reaches a running worker."""
        if time.monotonic() - self._policy_at > 60:
            from screener.policy import effective_config

            try:
                with self.factory() as s:
                    self._policy_cfg = effective_config(s, self.cfg)
            except Exception:  # fail soft: keep the last good policy
                log.exception("could not read the active AI Policy")
            self._policy_at = time.monotonic()
        return self._policy_cfg

    async def run_light(self, task: Task) -> dict[str, Any]:
        # Short sessions only: read, let go of the connection during the web lookups, then write the verdict.
        with self.factory() as s:
            p = s.get(Prospect, task.subject_id)
            if p is None:
                return {"skipped": "prospect gone"}
            if already_classified(p):
                return {"skipped": f"already classified ({p.verdict})"}  # never pay twice
            registry = list(s.scalars(select(RegistryEntity).where(RegistryEntity.id.in_(p.registry_ids or []))))
            s.expunge_all()  # plain read-only objects from here on
        outcome = await classify(p, registry, cfg=self.policy_cfg(), fetcher=self.fetcher, router=self.router,
                                 gemini=self.gemini, quota=self.quota, session_factory=self.factory)
        with self.factory() as s:
            p = s.get(Prospect, task.subject_id)
            record(s, p, outcome, int(self.lconf["refresh_days"]))
            if outcome.verdict == "fit" and outcome.company_id and self.wconf.get("deep_after_light", True):
                enqueue(s, "deep", outcome.company_id, p.triage_score + 200.0)  # fits jump the light queue
            s.commit()
            self.stats.bump(self.stats.verdicts, outcome.verdict)
            self.stats.llm_calls += outcome.llm_calls
            return {"verdict": outcome.verdict, "reason": outcome.reason, "company_id": str(outcome.company_id or "")}

    async def run_deep(self, task: Task) -> dict[str, Any]:
        from screener.enrich.service import Enricher, load_targets

        with self.factory() as s:
            company = s.get(Company, task.subject_id)
            if company is None:
                return {"skipped": "company gone"}
            targets = load_targets(s, [company])
        report = await Enricher(self.policy_cfg(), fetcher=self.fetcher, router=self.router, gemini=self.gemini,
                                quota=self.quota).enrich(self.factory, targets)
        with self.factory() as s:
            enqueue(s, "verify", task.subject_id, task.priority)
            s.commit()
        return {"stored": report.stored, "drafts": report.drafts, "rejected": report.rejected_grounding,
                "errors": report.errors[:5]}

    async def run_verify(self, task: Task) -> dict[str, Any]:
        from screener.verify.service import Verifier

        with self.factory() as s:
            company = s.get(Company, task.subject_id)
            if company is None:
                return {"skipped": "company gone"}
            report = await Verifier(self.cfg, fetcher=self.fetcher, gemini=self.gemini).run(s, [company])
        return {"checked": report.claims_checked, "by_status": dict(report.by_status), "liveness": dict(report.liveness)}

    def _requeue(self, task: Task, reason: str, run_after: Any) -> None:
        """Put a task back without spending one of its attempts (budget, not a failure)."""
        with self.factory() as s:
            t = s.get(Task, task.id)
            t.status, t.locked_until, t.error = "queued", None, reason
            t.attempts = max(0, t.attempts - 1)
            t.run_after = run_after
            s.commit()

    def _refused_since(self, t0: float) -> LLMBudgetExceeded | None:
        block = getattr(self.gemini, "last_block", None)
        return block[1] if block is not None and block[0] >= t0 else None

    async def handle(self, task: Task) -> None:
        runner = {"light": self.run_light, "deep": self.run_deep, "verify": self.run_verify}[task.kind]
        t0 = time.monotonic()
        try:
            result = await runner(task)
        except BudgetExhausted as exc:
            self._requeue(task, str(exc), _tomorrow())
            raise
        except LLMBudgetExceeded as exc:
            self._requeue(task, str(exc), _cap_reset(exc.scope))
            raise
        except Exception as exc:  # fail soft: the task is retried with backoff, the worker continues
            log.exception("task %s %s failed", task.kind, task.subject_id)
            with self.factory() as s:
                fail(s, s.get(Task, task.id), f"{type(exc).__name__}: {exc}", int(self.lconf["max_attempts"]))
            self.stats.failed += 1
            return
        refused = self._refused_since(t0)
        if refused is not None:  # a Gemini spend cap was hit mid-task (stages fail soft): redo it after the reset
            self._requeue(task, str(refused), _cap_reset(refused.scope))
            raise refused
        with self.factory() as s:
            finish(s, s.get(Task, task.id), result)
        self.stats.bump(self.stats.done, task.kind)

    async def run(self, kinds: list[str], *, max_tasks: int | None = None, until_empty: bool = False) -> WorkerStats:
        concurrency = int(self.wconf["concurrency"])
        handled = 0
        while max_tasks is None or handled < max_tasks:
            n = concurrency if max_tasks is None else min(concurrency, max_tasks - handled)
            with self.factory() as s:
                tasks = claim(s, kinds, n, int(self.wconf["lock_seconds"]))
            if not tasks:
                if until_empty:
                    break
                await asyncio.sleep(float(self.wconf["idle_sleep_seconds"]))
                continue
            results = await asyncio.gather(*(self.handle(t) for t in tasks), return_exceptions=True)
            handled += len(tasks)
            capped = next((r for r in results if isinstance(r, LLMBudgetExceeded)), None)
            if capped is not None:
                wake = _cap_reset(capped.scope)
                log.warning("Gemini spend cap reached (%s); worker paused until %s", capped, wake.isoformat())
                if until_empty:
                    break
                await asyncio.sleep(max(60.0, (wake - _now()).total_seconds()))
                continue
            if any(isinstance(r, BudgetExhausted) for r in results):
                # Light tasks were rescheduled for tomorrow (run_after); deep/verify tasks keep flowing.
                log.warning("daily light-tier LLM budget reached; light tasks resume tomorrow")
                if until_empty:
                    break
            log.warning("worker progress: %s; verdicts %s; failed %d", self.stats.done, self.stats.verdicts, self.stats.failed)
        return self.stats


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _cap_reset(scope: str):
    """When a refused call can be retried: day caps reset tomorrow (UTC), month caps on the 1st."""
    from datetime import datetime, timezone

    if scope == "month":
        now = _now()
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return datetime(year, month, 1, 0, 5, tzinfo=timezone.utc)
    return _tomorrow()


def _tomorrow():
    from datetime import datetime, time, timedelta, timezone

    now = datetime.now(timezone.utc)
    return datetime.combine(now.date() + timedelta(days=1), time(0, 5), tzinfo=timezone.utc)
