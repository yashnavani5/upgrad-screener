"""`screener run`: discover -> resolve -> enrich -> verify -> score -> explain -> publish, as one resumable run.

State machine: each stage writes a checkpoint into runs.checkpoints
    {stage: {status, started_at, finished_at, summary, retry}}
status: running | done | partial | skipped | failed. `--resume <run_id>` reloads the run's options and continues
from the first stage that is not `done` (budget-skipped / partial / failed / interrupted stages are retried;
stages skipped by choice, e.g. --no-publish, stay skipped). Within a stage, work already finished is not redone:
enrichment skips companies enriched during this run (and, incrementally, recently), verification only checks
unverified claims, score rewrites its own rows for the run.

Budget guard: before each LLM-heavy stage the remaining Gemini budget (per-run cap, optional daily cap) sizes
the stage; what does not fit is deferred and recorded, so the run ends `partial` instead of exceeding a quota."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from screener.budget import BudgetGuard, from_config
from screener.models import Candidate, Claim, Company, CompanyFounder, Judgment, Run, RunFunnel

log = logging.getLogger(__name__)

STAGES = ("discover", "resolve", "enrich", "verify", "score", "explain", "publish")
DONE = "done"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StageResult:
    status: str                 # done | partial | skipped
    summary: str
    retry: bool = False         # a later --resume should run this stage again (budget / partial)


@dataclass
class PipelineReport:
    run_id: uuid.UUID
    status: str = "running"
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    resumed_from: str | None = None


def should_run(checkpoint: dict[str, Any] | None) -> bool:
    """A stage runs unless it finished, or was deliberately skipped (not for budget)."""
    if not checkpoint:
        return True
    status = checkpoint.get("status")
    if status == DONE:
        return False
    if status == "skipped" and not checkpoint.get("retry"):
        return False
    return True


def final_status(checkpoints: dict[str, Any]) -> str:
    if (checkpoints.get("score") or {}).get("status") != DONE:
        return "failed"
    degraded = any((checkpoints.get(s) or {}).get("status") != DONE for s in STAGES
                   if not ((checkpoints.get(s) or {}).get("status") == "skipped" and not (checkpoints.get(s) or {}).get("retry")))
    return "partial" if degraded else "succeeded"


class Pipeline:
    def __init__(self, cfg: Any, factory: sessionmaker[Session], *, gemini: Any, quota: Any,
                 progress: Callable[[str], None] = lambda m: None) -> None:
        self.cfg, self.factory, self.gemini, self.quota, self.progress = cfg, factory, gemini, quota, progress
        if gemini is not None and quota is not None:
            gemini.usage_store = quota  # calls per day + shared USD spend caps (no rate limiting)
        self.guard: BudgetGuard = from_config(cfg, gemini, quota)

    # --- checkpoints -------------------------------------------------------------------------------

    def _checkpoint(self, s: Session, run: Run, stage: str, **fields: Any) -> None:
        cps = dict(run.checkpoints or {})
        cps[stage] = {**(cps.get(stage) or {}), **fields}
        run.checkpoints = cps
        flag_modified(run, "checkpoints")
        run.current_stage, run.heartbeat_at = stage, utcnow()
        s.commit()

    def _heartbeat(self, run_id: uuid.UUID, done: int, total: int) -> None:
        with self.factory() as s:
            run = s.get(Run, run_id)
            run.stage_progress = done / max(1, total)
            run.heartbeat_at = utcnow()
            s.commit()

    # --- run / resume ------------------------------------------------------------------------------

    async def run(self, *, top: int = 150, limit_per_scout: int = 30, scouts: str = "all", publish: bool = True,
                  websites: bool = True, pairwise: bool = True, incremental: bool = True, trigger: str = "cli",
                  resume: uuid.UUID | None = None) -> PipelineReport:
        with self.factory() as s:
            from screener.policy import effective_config

            self.cfg = effective_config(s, self.cfg)  # every stage follows the active AI Policy version
            if resume is not None:
                run = s.get(Run, resume)
                if run is None:
                    raise ValueError(f"run {resume} not found")
                if run.status == "succeeded":
                    return PipelineReport(run.id, run.status, dict(run.checkpoints or {}), "nothing to resume")
                run.status, run.heartbeat_at = "running", utcnow()
                options = dict(run.options or {})
            else:
                options = {"top": top, "limit_per_scout": limit_per_scout, "scouts": scouts, "publish": publish,
                           "websites": websites, "pairwise": pairwise, "incremental": incremental}
                run = Run(id=uuid.uuid4(), trigger=trigger, status="running", started_at=utcnow(), heartbeat_at=utcnow(),
                          config_snapshot=self.cfg.snapshot(), options=options, checkpoints={})
                s.add(run)
            s.commit()
            run_id = run.id
        report = PipelineReport(run_id)
        if resume is not None:
            report.resumed_from = next((st for st in STAGES if should_run((run.checkpoints or {}).get(st))), None)

        from screener.http import Fetcher
        from screener.search import SearchRouter

        async with httpx.AsyncClient(headers={"User-Agent": self.cfg.settings.user_agent}, timeout=20.0) as client, \
                Fetcher(self.cfg) as fetcher:
            router = SearchRouter.from_config(self.cfg, client=client, quota=self.quota, gemini=self.gemini)
            ctx = {"fetcher": fetcher, "router": router, "client": client, "options": options}
            with self.factory() as s:
                run = s.get(Run, run_id)
                base_cost = float(run.cost_usd or 0.0)  # a resumed run keeps what it already spent
                for stage in STAGES:
                    cp = (run.checkpoints or {}).get(stage)
                    if not should_run(cp):
                        self.progress(f"[{STAGES.index(stage) + 1}/7] {stage}: already {cp['status']} - skipping")
                        continue
                    self.progress(f"[{STAGES.index(stage) + 1}/7] {stage}")
                    run.stage_progress = 0.0
                    self._checkpoint(s, run, stage, status="running", started_at=utcnow().isoformat(), finished_at=None)
                    try:
                        result: StageResult = await getattr(self, f"_{stage}")(s, run, ctx)
                    except Exception as exc:  # fail soft per stage; score failing fails the run
                        log.exception("stage %s failed", stage)
                        s.rollback()
                        run = s.get(Run, run_id)
                        result = StageResult("failed", f"{type(exc).__name__}: {str(exc)[:300]}", retry=True)
                        prior = (run.error_summary or "").rstrip()
                        sep = "" if not prior or prior.endswith(";") else ";"
                        run.error_summary = (f"{prior}{sep} {stage}: {type(exc).__name__};".lstrip())[:2000]
                    if self.gemini is not None:
                        run.cost_usd = round(base_cost + float(getattr(self.gemini, "spent_usd", 0.0)), 4)
                    self._checkpoint(s, run, stage, status=result.status, summary=result.summary, retry=result.retry,
                                     finished_at=utcnow().isoformat())
                    self.progress(f"    -> {result.status}: {result.summary}")
                    if stage == "score" and result.status == "failed":
                        break
                run.status = final_status(run.checkpoints or {})
                run.finished_at = run.heartbeat_at = utcnow()
                run.stage_progress = 1.0
                s.commit()
                report.status, report.stages = run.status, dict(run.checkpoints or {})
        return report

    # --- stages ------------------------------------------------------------------------------------

    async def _discover(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.scouts import SCOUTS, ScoutContext, persist, run_scouts

        o = ctx["options"]
        names = list(SCOUTS) if o["scouts"] == "all" else [x.strip() for x in o["scouts"].split(",") if x.strip()]
        sctx = ScoutContext(cfg=self.cfg, fetcher=ctx["fetcher"], router=ctx["router"], http=ctx["client"], gemini=self.gemini)
        reports = await run_scouts([SCOUTS[n](sctx) for n in names], int(o["limit_per_scout"]))
        new = sum(persist(r.items, run.id) for r in reports)  # dedupe key: re-running never duplicates
        found = sum(len(r.items) for r in reports)
        run.counts = {**(run.counts or {}), "candidates": found}
        flag_modified(run, "counts")
        return StageResult(DONE, f"{found} candidates ({new} new) from {sum(1 for r in reports if r.items)} scouts")

    async def _resolve(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.embed import Embedder
        from screener.resolve.service import resolve

        r = await resolve(s, self.cfg, gemini=self.gemini, embedder=Embedder(self.cfg, gemini=self.gemini),
                          router=ctx["router"], fetcher=ctx["fetcher"], find_websites=ctx["options"]["websites"])
        s.commit()
        return StageResult(DONE, f"{r.discoveries} candidates -> {r.companies} companies ({r.companies_created} new) -> {r.founders} founders")

    def _ranked(self, s: Session, top: int) -> list[Company]:
        from screener.enrich.priority import rank_companies

        return [c for c, _ in rank_companies(s, self.cfg.enrich["priority"])[:top]]

    def enrich_targets(self, s: Session, run: Run, top: int, incremental: bool) -> tuple[list[Company], int, int]:
        """(to enrich, done in this run, fresh enough). Never redoes a company finished during this run."""
        stale = timedelta(days=int(self.cfg.enrich.get("incremental", {}).get("enrich_stale_days", 30)))
        out, done_here, fresh = [], 0, 0
        for c in self._ranked(s, top):
            if c.enriched_at and c.enriched_at >= run.started_at:
                done_here += 1
            elif incremental and c.enriched_at and c.enriched_at > utcnow() - stale:
                fresh += 1
            else:
                out.append(c)
        return out, done_here, fresh

    async def _enrich(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.enrich.service import Enricher, load_targets

        o = ctx["options"]
        todo, done_here, fresh = self.enrich_targets(s, run, int(o["top"]), bool(o["incremental"]))
        fit = self.guard.affordable(len(todo), "enrich_per_company")
        deferred = len(todo) - fit
        targets = load_targets(s, todo[:fit])
        r = await Enricher(self.cfg, fetcher=ctx["fetcher"], router=ctx["router"], gemini=self.gemini, quota=self.quota).enrich(
            self.factory, targets, should_stop=self.guard.exhausted,
            on_progress=lambda d, t: self._heartbeat(run.id, d, t))
        deferred += r.skipped_budget
        summary = (f"{len(targets) - r.skipped_budget} enriched ({r.stored} new claims); skipped {done_here} already done in "
                   f"this run, {fresh} fresh (< stale_days)")
        summary += await self._lookups(s, run, ctx, Enricher(self.cfg, fetcher=ctx["fetcher"], router=ctx["router"],
                                                        gemini=self.gemini, quota=self.quota))
        if deferred:
            return StageResult("partial", summary + f"; {deferred} deferred by the budget guard", retry=True)
        return StageResult(DONE, summary)

    async def _lookups(self, s: Session, run: Run, ctx: dict[str, Any], enricher: Any) -> str:
        """Targeted size / location / founder lookups where data is missing (fail soft, never paid twice)."""
        from screener.enrich.lookups import Lookups, lookup_targets
        from screener.score.features import load

        try:
            conf = self.cfg.enrich["lookups"]
            companies = lookup_targets(s, self.cfg, int(conf["top_n"]), self._ranked(s, int(ctx["options"]["top"])))
            r = await Lookups(enricher).run(self.factory, load(s, self.cfg, companies), should_stop=self.guard.exhausted,
                                            on_progress=lambda d, t: self._heartbeat(run.id, d, t))
            s.expire_all()
            return (f"; lookups for {r.companies} companies (size {r.size}, location {r.location}, founders {r.founders}, "
                    f"X profiles {r.x_profiles}; "
                    f"{r.stored} new claims, {len(r.errors)} errors)")
        except Exception as exc:
            log.warning("lookups skipped: %s: %s", type(exc).__name__, exc)
            return f"; lookups skipped ({type(exc).__name__})"

    async def _verify(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.verify.service import Verifier

        companies = self._ranked(s, int(ctx["options"]["top"]))
        ids = [c.id for c in companies]
        founder_ids = select(CompanyFounder.founder_id).where(CompanyFounder.company_id.in_(ids))
        sources = s.scalar(select(func.count(func.distinct(Claim.source_url))).where(
            Claim.verified_status == "unverified",
            ((Claim.entity_type == "company") & Claim.entity_id.in_(ids)) | ((Claim.entity_type == "founder") & Claim.entity_id.in_(founder_ids)),
        )) or 0
        cap = self.guard.affordable(sources, "verify_per_source")
        r = await Verifier(self.cfg, fetcher=ctx["fetcher"], gemini=self.gemini).run(
            s, companies, max_sources=cap if cap < sources else None,
            on_progress=lambda d, t: self._heartbeat(run.id, d, t))
        summary = f"{r.claims_checked} claims from {r.sources} sources {dict(r.by_status)}"
        if r.sources_deferred:
            return StageResult("partial", summary + f"; {r.sources_deferred} sources deferred by the budget guard", retry=True)
        return StageResult(DONE, summary)

    async def _score(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.embed import Embedder
        from screener.score.service import score

        # Read fresh: enrich/lookups and verify write claims through other sessions, and this long-lived session
        # must not score from stale identity-map copies (a 2026-10-25 run scored verified locations as unknown).
        s.expire_all()
        # Resume-safe: drop any rows a crashed attempt left for this run.
        s.execute(delete(Candidate).where(Candidate.run_id == run.id))
        s.execute(delete(Judgment).where(Judgment.run_id == run.id))
        s.execute(delete(RunFunnel).where(RunFunnel.run_id == run.id))
        s.commit()
        o = ctx["options"]
        top_n = int(self.cfg.weights["pairwise"]["top_n"])
        pairwise = bool(o["pairwise"]) and self.guard.fits(top_n, "pairwise_per_item")
        note = "" if pairwise or not o["pairwise"] else "; pairwise judge skipped by the budget guard"
        from screener.policy import effective_config

        cfg = effective_config(s, self.cfg)  # YAML overlaid by the active AI Policy version
        r = await score(s, cfg, embedder=Embedder(cfg, gemini=self.gemini), gemini=self.gemini, pairwise=pairwise,
                        run=run, finalize=False, on_rows=self._long_list_streamer(s) if o["publish"] else None)
        judge = "judge reused from the last run" if r.judge_reused else f"{r.judgments} judged pairs"
        summary = f"{len(r.scored)} scored, shortlist {len(r.shortlist)}, {judge}{note}"
        summary += self._refresh_seeds(s, run.id)
        return StageResult("partial" if note else DONE, summary, retry=bool(note))

    def _refresh_seeds(self, s: Session, run_id: uuid.UUID) -> str:
        """Auto calibration seeds from this run's verified data (fail soft: never fails the score stage)."""
        from screener.config import CONFIG_DIR
        from screener.score.seeds import refresh

        try:
            s.commit()
            r = refresh(s, CONFIG_DIR / "seeds.yaml", self.cfg.seeds, self.cfg.weights["calibration"].get("seeds", {}), run_id)
            return f"; seeds {r['positives']} positive / {r['negatives']} negative (auto)"
        except Exception as exc:  # e.g. a read-only config mount in Docker
            log.warning("seed refresh skipped: %s: %s", type(exc).__name__, exc)
            return "; seed refresh skipped"

    async def _explain(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        from screener.explain.service import explain_run

        s.expire_all()  # fresh claims (see _score)
        n = int(self.cfg.weights["mmr"]["shortlist_max"])
        gemini = self.gemini if self.guard.fits(n, "explain_per_company") else None
        from screener.policy import effective_config

        r = await explain_run(s, effective_config(s, self.cfg), gemini, run)
        summary = (f"{r.written} written ({r.llm} LLM, {r.template} from claims, {r.regenerated} regenerated); "
                   f"{r.founder_lines} founder lines; curated left out: {', '.join(r.curated) or 'none'}")
        if gemini is None:
            return StageResult("partial", summary + "; LLM writer skipped by the budget guard (claim templates used)", retry=True)
        return StageResult(DONE, summary)

    async def _publish(self, s: Session, run: Run, ctx: dict[str, Any]) -> StageResult:
        if not ctx["options"]["publish"]:
            return StageResult("skipped", "--no-publish")
        pub = self._publisher()
        if pub is None:
            return StageResult("skipped", "GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID not set", retry=True)
        from screener.publish.service import publish as do_publish

        r = do_publish(s, pub, run)
        return StageResult(DONE, f"{r.counts} -> {r.url}")

    def _publisher(self) -> Any:
        if not hasattr(self, "_pub"):
            from screener.publish.sheets import SheetPublisher

            st = self.cfg.settings
            self._pub = SheetPublisher.from_settings(st) if st.google_service_account_json and st.google_sheet_id else None
        return self._pub

    def _long_list_streamer(self, s: Session) -> Callable[[list[tuple[Any, Any, Any]]], None] | None:
        pub = self._publisher()
        if pub is None:
            return None
        from screener.publish.service import _founders, long_list_record

        def stream(rows: list[tuple[Any, Any, Any]]) -> None:
            pub.upsert("Long list", [long_list_record(sc, co, cand, _founders(s, co.id), None, None) for sc, co, cand in rows])

        return stream
