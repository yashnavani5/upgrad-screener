"""Universe persistence: idempotent registry upserts, loaders (MCA API, DPIIT CSV, scouts), cross-validation,
prospect building + triage, and the DB-backed task queue."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from screener.models import Discovery, LoadCheckpoint, Prospect, RegistryEntity, Task
from screener.resolve.normalize import registered_domain
from screener.scouts.prefilter import Prefilter
from screener.universe.records import (
    RegistryRow, cross_validate, dpiit_row, mca_row, merge_key, triage, validate_row,
)

log = logging.getLogger(__name__)
BATCH = 1000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _insert(session: Session):
    return pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert


# --- registry upserts --------------------------------------------------------------------------------


def upsert_rows(session: Session, rows: list[RegistryRow], vconf: dict[str, Any]) -> int:
    """Insert or update registry rows (by source + source_id). Unchanged rows only bump last_seen_at."""
    if not rows:
        return 0
    now = utcnow()
    unique: dict[tuple[str, str], RegistryRow] = {(r.source, r.source_id): r for r in rows}
    values = []
    for r in unique.values():
        status, issues = validate_row(r, vconf)
        values.append({
            "id": uuid.uuid4(), "source": r.source, "source_id": r.source_id[:120], "name": r.name[:400],
            "normalized_name": r.normalized_name[:400], "cin": r.cin, "nic_code": r.nic_code, "status": r.status,
            "registered_on": r.registered_on, "paid_up_capital_inr": r.paid_up_capital_inr, "state": r.state,
            "city": r.city, "activity": r.activity, "sector": r.sector, "website": r.website,
            "raw": {**r.raw, "_aliases": r.aliases} if r.aliases else r.raw, "content_hash": r.content_hash(),
            "validation_status": status, "validation_issues": issues, "first_seen_at": now, "last_seen_at": now,
        })
    t = RegistryEntity.__table__
    stmt = _insert(session)(t).values(values)
    excluded = stmt.excluded
    keep = ("name", "normalized_name", "cin", "nic_code", "status", "registered_on", "paid_up_capital_inr", "state",
            "city", "activity", "sector", "website", "raw", "content_hash", "validation_status", "validation_issues",
            "last_seen_at")
    stmt = stmt.on_conflict_do_update(index_elements=["source", "source_id"], set_={k: excluded[k] for k in keep})
    session.execute(stmt)
    return len(values)


def _batches(items: Iterable[RegistryRow], size: int = BATCH) -> Iterator[list[RegistryRow]]:
    batch: list[RegistryRow] = []
    for it in items:
        batch.append(it)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_dpiit_csv(session: Session, path: Path, universe: dict[str, Any]) -> int:
    """Stream the Dataful DPIIT CSV export into registry_entities (UTF-8 with BOM or cp1252)."""
    fields, pattern = universe["dpiit"]["fields"], universe["validation"]["cin_pattern"]
    total = 0
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(encoding=encoding, newline="") as fh:
                rows = (r for raw in csv.DictReader(fh) if (r := dpiit_row(raw, fields, pattern)) is not None)
                for batch in _batches(rows):
                    total += upsert_rows(session, batch, universe["validation"])
                    session.commit()
            return total
        except UnicodeDecodeError:
            session.rollback()
            total = 0
            continue
    raise ValueError(f"cannot decode {path} as UTF-8 or cp1252")


async def load_mca(session: Session, fetcher: Any, api_key: str, universe: dict[str, Any], *,
                   max_pages: int | None = None, keep: Any = None) -> dict[str, int]:
    """Page through data.gov.in MCA company-master resources; resumable via load_checkpoints.
    `keep(row)`: store only rows it accepts (education candidates); every row still advances the checkpoint."""
    conf = universe["mca"]
    fetcher.limiter.configure("provider:datagovin", float(conf.get("rpm", 30)))
    counts: dict[str, int] = {}
    for resource in conf.get("resource_ids") or []:
        cp = session.get(LoadCheckpoint, ("mca", resource)) or LoadCheckpoint(source="mca", resource=resource, next_offset=0)
        session.add(cp)
        if cp.done:
            continue
        pages = 0
        while not cp.done and (max_pages is None or pages < max_pages):
            params = {"api-key": api_key, "format": "json", "limit": int(conf["page_size"]), "offset": cp.next_offset}
            params |= {f"filters[{k}]": v for k, v in (conf.get("server_filters") or {}).items()}
            attempts = int(conf.get("page_retries", 5))
            for attempt in range(1, attempts + 1):
                try:
                    data = await fetcher.get_json(conf["endpoint"].format(resource_id=resource), params=params,
                                                  provider="datagovin", use_cache=False,
                                                  max_bytes=int(conf.get("max_bytes", 30_000_000)))
                    break
                except Exception as exc:  # dropped connections on 9 MB pages are common; the checkpoint is safe
                    if attempt == attempts:
                        raise
                    wait = min(120.0, 5.0 * 2 ** (attempt - 1))
                    log.warning("mca %s offset %d: %s (attempt %d/%d), retrying in %.0fs",
                                resource[:8], cp.next_offset, type(exc).__name__, attempt, attempts, wait)
                    await asyncio.sleep(wait)
            records = data.get("records") or []
            rows = [r for raw in records if (r := mca_row(raw, conf["fields"], universe["validation"]["cin_pattern"]))
                    and (keep is None or keep(r))]
            counts[resource] = counts.get(resource, 0) + upsert_rows(session, rows, universe["validation"])
            cp.total = int(data.get("total") or 0) or cp.total
            cp.next_offset += len(records)
            cp.done = not records or (cp.total is not None and cp.next_offset >= cp.total)
            cp.updated_at = utcnow()
            session.commit()
            pages += 1
            log.warning("mca %s: %d/%s scanned, %d stored so far", resource[:8], cp.next_offset, cp.total, counts[resource])
    return counts


def load_scouts(session: Session) -> int:
    """Discoveries join the universe as registry rows (source 'scout'), so scout hits boost triage."""
    rows = []
    for d in session.scalars(select(Discovery)):
        website = d.company_url if d.company_url and registered_domain(d.company_url) else None
        rows.append(RegistryRow(source="scout", source_id=str(d.id), name=d.raw_company_name, website=website,
                                sector=d.scout, raw={"scout": d.scout, "source_url": d.source_url,
                                                     "company_id": str(d.company_id) if d.company_id else None}))
    n = 0
    for batch in _batches(rows):
        n += upsert_rows(session, batch, {"cin_pattern": ".^", "llpin_pattern": ".^"})
        session.commit()
    return n


async def load_yc(session: Session, fetcher: Any, sources: dict[str, Any]) -> int:
    """All education companies from the public yc-oss directory (same filter as the accelerators scout)."""
    from screener.scouts.accelerators import YC_MAX_BYTES, filter_yc

    yc = sources["accelerators"]["yc"]
    data = await fetcher.get_json(yc["endpoint"], provider="yc-oss", max_bytes=YC_MAX_BYTES)
    rows = []
    for c in filter_yc(data, yc.get("industries", []), yc.get("tag_keywords", [])):
        website = c.get("website") if c.get("website") and registered_domain(c.get("website")) else None
        city = (c.get("all_locations") or "").split(",")[0].strip() or None
        rows.append(RegistryRow(source="yc", source_id=str(c.get("id") or c.get("slug")), name=c["name"], website=website,
                                city=city, status="Active" if (c.get("status") or "").lower() == "active" else c.get("status"),
                                sector=" | ".join([c.get("industry") or "", c.get("subindustry") or "", *(c.get("tags") or [])]),
                                raw={k: c.get(k) for k in ("slug", "batch", "one_liner", "url", "regions", "team_size")}))
    n = 0
    for batch in _batches(rows):
        n += upsert_rows(session, batch, {"cin_pattern": ".^", "llpin_pattern": ".^"})
        session.commit()
    return n


# --- cross-validation --------------------------------------------------------------------------------


def _row(e: RegistryEntity) -> RegistryRow:
    return RegistryRow(source=e.source, source_id=e.source_id, name=e.name, cin=e.cin, nic_code=e.nic_code,
                       status=e.status, registered_on=e.registered_on, paid_up_capital_inr=e.paid_up_capital_inr,
                       state=e.state, city=e.city, activity=e.activity, sector=e.sector, website=e.website,
                       aliases=list((e.raw or {}).get("_aliases") or []), raw=e.raw or {})


def cross_validate_all(session: Session, vconf: dict[str, Any]) -> dict[str, int]:
    """DPIIT rows vs MCA rows with the same CIN: name/state agreement recorded on the DPIIT row."""
    counts: dict[str, int] = defaultdict(int)
    for e in session.scalars(select(RegistryEntity).where(RegistryEntity.source == "dpiit")):
        mca = session.scalar(select(RegistryEntity).where(RegistryEntity.source == "mca", RegistryEntity.cin == e.cin)) if e.cin else None
        issues = [i for i in (e.validation_issues or []) if not (i.startswith("ok:cross") or "mca" in i or i.endswith("no_cin"))]
        found = cross_validate(_row(e), _row(mca) if mca else None, vconf)
        e.validation_issues = issues + found
        e.cross_ref_id = mca.id if mca else None
        if e.validation_status != "invalid":
            e.validation_status = "warning" if any(i.startswith("warning:") for i in e.validation_issues) else "valid"
        for i in found:
            counts[i] += 1
    session.commit()
    return dict(counts)


# --- prospects + triage ------------------------------------------------------------------------------


def build_prospects(session: Session, universe: dict[str, Any], prefilter: Prefilter, *, batch: int = 5000) -> dict[str, int]:
    """Group registry rows into prospects (CIN, else name+state), score them in code, queue the light tier.
    A prospect whose rows did not change since its last check is left alone (no repeated work)."""
    pattern = universe["validation"]["cin_pattern"]
    today = utcnow().date()
    refresh = timedelta(days=int(universe["light"]["refresh_days"]))
    groups: dict[str, list[RegistryEntity]] = defaultdict(list)
    for e in session.scalars(candidate_rows(session, universe, prefilter).execution_options(yield_per=batch)):
        groups[merge_key(_row(e), pattern)].append(e)
    _attach_nameless(groups)

    existing = {p.merge_key: p for p in session.scalars(select(Prospect))}
    counts: dict[str, int] = defaultdict(int)
    for key, ents in groups.items():
        rows = [_row(e) for e in ents]
        issues = [i for e in ents for i in (e.validation_issues or [])]
        content = hashlib.sha256("|".join(sorted(e.content_hash for e in ents)).encode()).hexdigest()
        p = existing.get(key)
        if p is not None and p.content_hash == content:
            counts["unchanged"] += 1
            continue
        result = triage(rows, issues, universe, prefilter, today)
        best = max(rows, key=lambda r: (r.source == "mca", r.source == "dpiit", bool(r.website)))
        website = next((r.website for r in rows if r.website), None)
        if p is None:
            p = Prospect(id=uuid.uuid4(), merge_key=key[:200], name=best.name[:400], content_hash=content)
            session.add(p)
            counts["new"] += 1
        else:
            counts["changed"] += 1
        p.name, p.cin = best.name[:400], next((r.cin for r in rows if r.cin), None)
        p.website, p.domain = website, registered_domain(website) if website else None
        p.state = next((r.state for r in rows if r.state), None)
        p.sources = sorted({r.source for r in rows})
        p.registry_ids = [str(e.id) for e in ents]
        p.content_hash, p.triage_score, p.triage_reasons = content, result.score, result.reasons
        already_checked = p.processed_hash == content and p.next_check_at and p.next_check_at > utcnow()
        resolved = next((e.raw.get("company_id") for e in ents if (e.raw or {}).get("company_id")), None)
        if resolved and p.stage in (None, "new", "queued", "triaged_out"):
            # Already a canonical company from the scout/resolve path: no light-tier work needed.
            p.company_id, p.stage = uuid.UUID(resolved), "promoted"
            p.verdict_reason = "already resolved from discovery scouts"
            counts["already_resolved"] += 1
        elif not result.crawl:
            p.stage = "triaged_out" if p.stage in (None, "new", "triaged_out", "queued") else p.stage
            counts["triaged_out"] += 1
        elif not already_checked and p.stage not in ("promoted",):
            p.stage = "queued"
            session.flush()
            enqueue(session, "light", p.id, result.score)
            counts["queued"] += 1
        session.flush()
    session.commit()
    return dict(counts)


def candidate_rows(session: Session, universe: dict[str, Any], prefilter: Prefilter):
    """Only rows that can reach the triage threshold: DPIIT/scout rows, MCA rows with an education NIC code or
    an education keyword in the name, and MCA rows sharing a CIN with a DPIIT row. Every other MCA row scores
    at most `recent` (< min_score_for_light), so skipping it here gives the same result without loading
    lakhs of rows into memory."""
    from sqlalchemy import or_

    nic = [RegistryEntity.nic_code.like(p + "%") for p in universe["education"]["nic_prefixes"]]
    dpiit_cins = select(RegistryEntity.cin).where(RegistryEntity.source == "dpiit", RegistryEntity.cin.is_not(None))
    conds = [RegistryEntity.source.in_(["dpiit", "scout", "yc"]), *nic, RegistryEntity.cin.in_(dpiit_cins)]
    if session.get_bind().dialect.name == "postgresql":
        conds.append(RegistryEntity.name.op("~*")(prefilter_regex(prefilter)))
        return select(RegistryEntity).where(or_(*conds))
    return select(RegistryEntity)  # SQLite (tests): small tables, triage filters in Python


def prefilter_regex(prefilter: Prefilter) -> str:
    """The prefilter as a Postgres regex (\\m / \\M are word boundaries)."""
    import re as _re

    parts = []
    for kw in sorted({k.strip().lower() for k in prefilter.keywords if k.strip()}, key=len, reverse=True):
        tokens = [t for t in _re.split(r"[\s\-]+", kw) if t]
        parts.append(r"[\s\-]*".join(_re.escape(t) for t in tokens))
    return r"\m(" + "|".join(parts) + r")\M"


def _attach_nameless(groups: dict[str, list[RegistryEntity]]) -> None:
    """Scout rows have no state/CIN: attach them to the one CIN prospect with the same normalized name."""
    by_name: dict[str, list[str]] = defaultdict(list)
    for key, ents in groups.items():
        if key.startswith("cin:"):
            for e in ents:
                by_name[e.normalized_name].append(key)
    for key in [k for k in groups if k.startswith("name:") and k.endswith("|")]:
        norm = key[len("name:") : -1]
        targets = sorted(set(by_name.get(norm, [])))
        if len(targets) == 1:
            groups[targets[0]].extend(groups.pop(key))


# --- task queue --------------------------------------------------------------------------------------


def enqueue(session: Session, kind: str, subject_id: uuid.UUID, priority: float, *, force: bool = False) -> None:
    """Queue a task once. A finished task is only re-queued when `force` (e.g. the source data changed)."""
    t = Task.__table__
    stmt = _insert(session)(t).values(id=uuid.uuid4(), kind=kind, subject_id=subject_id, status="queued",
                                      priority=priority, attempts=0, run_after=utcnow(), result={},
                                      created_at=utcnow(), updated_at=utcnow())
    if force:
        stmt = stmt.on_conflict_do_update(index_elements=["kind", "subject_id"],
                                          set_={"status": "queued", "priority": priority, "attempts": 0, "updated_at": utcnow()})
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=["kind", "subject_id"],
            set_={"status": "queued", "priority": priority, "updated_at": utcnow()},
            where=(t.c.status == "failed") | (t.c.status == "queued"),
        )
    session.execute(stmt)


def claim(session: Session, kinds: list[str], limit: int, lock_seconds: int) -> list[Task]:
    """Claim up to `limit` runnable tasks (highest priority first). Postgres: FOR UPDATE SKIP LOCKED, so
    several workers never take the same task. Expired locks (crashed workers) are reclaimable."""
    now = utcnow()
    until = now + timedelta(seconds=lock_seconds)
    q = (select(Task.id).where(Task.kind.in_(kinds), Task.run_after <= now,
                               (Task.status == "queued") | ((Task.status == "running") & (Task.locked_until < now)))
         .order_by(Task.priority.desc(), Task.created_at).limit(limit))
    if session.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    ids = list(session.scalars(q))
    if not ids:
        session.commit()
        return []
    session.execute(update(Task).where(Task.id.in_(ids)).values(status="running", locked_until=until,
                                                                attempts=Task.attempts + 1, updated_at=now))
    session.commit()
    return list(session.scalars(select(Task).where(Task.id.in_(ids))))


def finish(session: Session, task: Task, result: dict[str, Any]) -> None:
    task.status, task.result, task.error, task.locked_until = "done", result, None, None
    session.commit()


def fail(session: Session, task: Task, error: str, max_attempts: int) -> None:
    """Retry with backoff (5 min, 20 min, ...) until max_attempts, then mark failed."""
    task.error = error[:2000]
    task.locked_until = None
    if task.attempts >= max_attempts:
        task.status = "failed"
    else:
        task.status = "queued"
        task.run_after = utcnow() + timedelta(minutes=5 * 4 ** (task.attempts - 1))
    session.commit()


def queue_stats(session: Session) -> list[tuple[str, str, int]]:
    return [tuple(r) for r in session.execute(text("select kind, status, count(*) from tasks group by 1, 2 order by 1, 2"))]
