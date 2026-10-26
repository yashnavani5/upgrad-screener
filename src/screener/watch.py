"""`screener watch`: live view of whatever the pipeline is doing (any process: CLI run, worker, Docker).

Reads the shared call log (.cache/logs/screener.log, every external call is logged) and the database.
Read-only: it never changes anything."""

from __future__ import annotations

import re
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import text

CALL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO screener\.calls call provider=(?P<provider>\S+) "
    r"status=(?P<status>\S+) http=(?P<http>\S+) latency_ms=(?P<ms>\S+) cache=(?P<cache>\S+) endpoint=(?P<endpoint>.*?)(?: error=(?P<error>.*))?$"
)
PROGRESS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ WARNING \S+ (?P<msg>.*(progress|budget|stage).*)$")


def tail_lines(path: Path, max_bytes: int = 2_000_000) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        return fh.read().decode("utf-8", errors="replace").splitlines()


def parse(lines: list[str]) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    calls, progress = [], []
    for ln in lines:
        if m := CALL_RE.match(ln):
            calls.append(m.groupdict())
        elif m := PROGRESS_RE.match(ln):
            progress.append((m.group("ts"), m.group("msg")))
    return calls, progress


def snapshot(engine: Any, log_path: Path, now: datetime | None = None) -> Group:
    now = now or datetime.now()
    calls, progress = parse(tail_lines(log_path))
    window = now - timedelta(minutes=5)
    recent = [c for c in calls if datetime.strptime(c["ts"], "%Y-%m-%d %H:%M:%S") >= window]

    # Activity by provider (last 5 minutes)
    by = Counter((c["provider"], "ok" if c["status"] == "ok" else "err") for c in recent)
    lat: dict[str, list[int]] = {}
    for c in recent:
        if c["ms"].isdigit():
            lat.setdefault(c["provider"], []).append(int(c["ms"]))
    act = Table(title="activity, last 5 min", expand=True)
    for col in ("Provider", "OK", "Errors", "Per min", "Median ms"):
        act.add_column(col)
    for p in sorted({p for p, _ in by}):
        ok, err = by[(p, "ok")], by[(p, "err")]
        ms = sorted(lat.get(p, [0]))
        act.add_row(p, str(ok), str(err) if err else "-", f"{(ok + err) / 5:.1f}", str(ms[len(ms) // 2]))
    if not by:
        act.add_row("(idle)", "-", "-", "-", "-")

    # Last calls
    last = Table(title="latest external calls", expand=True)
    for col in ("Time", "Provider", "Status", "ms", "What"):
        last.add_column(col, overflow="ellipsis", no_wrap=True)
    for c in calls[-12:]:
        what = (c["endpoint"] or "")[:90]
        status = c["status"] if c["status"] == "ok" else f"[red]{c['status']}[/red]"
        last.add_row(c["ts"][11:], c["provider"], status, c["ms"], what)

    # Database state
    db = Table(title="database", expand=True)
    db.add_column("What")
    db.add_column("Count", justify="right")
    with engine.connect() as conn:
        def q(sql: str) -> Any:
            return conn.execute(text(sql)).all()

        run = q("select id, status, current_stage, stage_progress, started_at, heartbeat_at from runs order by started_at desc limit 1")
        for status, n in q("select verified_status, count(*) from claims group by 1 order by 1"):
            db.add_row(f"claims: {status}", str(n))
        for label, sql in (("companies", "select count(*) from companies"),
                           ("companies enriched", "select count(*) from companies where enriched_at is not null"),
                           ("companies verified", "select count(*) from companies where verified_at is not null"),
                           ("founders", "select count(*) from founders"),
                           ("prospects (universe)", "select count(*) from prospects")):
            db.add_row(label, str(q(sql)[0][0]))
        for kind, status, n in q("select kind, status, count(*) from tasks group by 1,2 order by 1,2"):
            db.add_row(f"tasks: {kind} / {status}", str(n))
        latest_claim = q("select max(coalesce(verified_at, created_at)) from claims")[0][0]
        latest_claim = latest_claim.astimezone() if latest_claim else None  # DB stores UTC; show local time

    run_line = "no scoring/pipeline run yet"
    if run:
        r = run[0]
        run_line = (f"run {str(r.id)[:8]}  status={r.status}  stage={r.current_stage or '-'}  "
                    f"progress={(r.stage_progress or 0):.0%}  started={r.started_at.astimezone():%H:%M}  "
                    f"heartbeat={r.heartbeat_at.astimezone():%H:%M:%S}"
                    if r.heartbeat_at else f"run {str(r.id)[:8]} status={r.status}")
    prog = "\n".join(f"{ts[11:]}  {msg[:150]}" for ts, msg in progress[-5:]) or "(no progress messages yet)"
    head = (f"[bold]screener watch[/bold]  {now:%H:%M:%S}   last claim write: "
            f"{latest_claim:%H:%M:%S}" if latest_claim else f"[bold]screener watch[/bold]  {now:%H:%M:%S}")
    return Group(Panel(f"{head}\n{run_line}", expand=True), act, last, db, Panel(prog, title="progress messages", expand=True))
