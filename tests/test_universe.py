"""Universe: CIN parsing, registry mapping, validation, cross-validation, triage, prospects and the queue."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from screener.config import load_yaml
from screener.models import Base, Prospect, RegistryEntity, Task
from screener.scouts.prefilter import Prefilter
from screener.universe.light import LightOutcome, already_classified, record
from screener.universe.records import (
    RegistryRow, cross_validate, dpiit_row, is_education_nic, mca_row, merge_key, parse_cin, pick, status_class,
    triage, validate_row,
)
from screener.universe.store import (
    build_prospects, claim, cross_validate_all, enqueue, fail, finish, load_dpiit_csv, upsert_rows,
)

U = load_yaml("universe")
V = U["validation"]
PF = Prefilter(load_yaml("sources")["prefilter_keywords"])
TODAY = date(2026, 10, 25)
# Synthetic CINs in the official format (not real companies): U + NIC 85xxx/80xxx/72xxx + state + year + type + seq.
EDU_CIN = "U85300KA2021PTC123456"
OLD_EDU_CIN = "U80903MH2011PTC654321"
IT_CIN = "U72900KA2015PTC111111"


# --- CIN ---------------------------------------------------------------------------------------------


def test_parse_cin() -> None:
    c = parse_cin(EDU_CIN, V["cin_pattern"])
    assert (c.listed, c.nic, c.state, c.year, c.company_type) == (False, "85300", "KA", 2021, "PTC")
    assert parse_cin("L80903MH2011PLC654321", V["cin_pattern"]).listed
    assert parse_cin("not-a-cin", V["cin_pattern"]) is None and parse_cin(None, V["cin_pattern"]) is None


def test_education_nic_both_nic_versions() -> None:
    prefixes = U["education"]["nic_prefixes"]
    assert is_education_nic("85300", prefixes)       # NIC-2008 division 85
    assert is_education_nic("80903", prefixes)       # NIC-2004 division 80 (older CINs)
    assert not is_education_nic("72900", prefixes)   # IT services
    assert not is_education_nic(None, prefixes)


# --- mapping -----------------------------------------------------------------------------------------


def test_pick_is_case_and_underscore_insensitive() -> None:
    row = {"COMPANY_NAME": "Acme Learning Private Limited", "Company Status": "Active", "x": "NA"}
    assert pick(row, ["company name"]) == "Acme Learning Private Limited"
    assert pick(row, ["company_status"]) == "Active"
    assert pick(row, ["x"]) is None  # 'NA' counts as empty


def test_mca_row_mapping() -> None:
    raw = {"CORPORATE_IDENTIFICATION_NUMBER": EDU_CIN, "COMPANY_NAME": "Acme Learning Private Limited",
           "COMPANY_STATUS": "Active", "DATE_OF_REGISTRATION": "14-03-2021", "PAIDUP_CAPITAL": "1,00,000",
           "REGISTERED_STATE": "Karnataka"}
    r = mca_row(raw, U["mca"]["fields"], V["cin_pattern"])
    assert (r.source_id, r.nic_code, r.registered_on, r.paid_up_capital_inr, r.state) == (
        EDU_CIN, "85300", date(2021, 3, 14), 100000.0, "Karnataka")
    assert mca_row({"COMPANY_NAME": "No CIN"}, U["mca"]["fields"], V["cin_pattern"]) is None


def test_dpiit_row_mapping() -> None:
    raw = {"State": "Karnataka", "City": "Bengaluru", "Company Name": "Acme", "Legal Name": "Acme Learning Pvt Ltd",
           "Cin": EDU_CIN, "Company Website": "acme.in", "Company Status": "Active", "Focus Industry": "Education",
           "Focus Sector": "EdTech", "Services Provided": "AI tutoring"}
    r = dpiit_row(raw, U["dpiit"]["fields"], V["cin_pattern"])
    assert r.website == "https://acme.in" and r.cin == EDU_CIN and r.aliases == ["Acme Learning Pvt Ltd"]
    assert "EdTech" in r.sector and r.city == "Bengaluru"


# --- validation --------------------------------------------------------------------------------------


def row(**kw) -> RegistryRow:
    base = dict(source="mca", source_id=EDU_CIN, name="Acme Learning Private Limited", cin=EDU_CIN, nic_code="85300",
                status="Active", registered_on=date(2021, 3, 14), state="Karnataka")
    return RegistryRow(**{**base, **kw})


def test_validate_row() -> None:
    assert validate_row(row(), V) == ("valid", [])
    assert validate_row(row(cin="BAD123", source_id="BAD123"), V)[0] == "invalid"
    assert validate_row(row(registered_on=date(2030, 1, 1)), V)[0] == "invalid"
    status, issues = validate_row(row(registered_on=date(2015, 1, 1)), V)
    assert status == "warning" and "warning:cin_year_mismatch" in issues
    assert validate_row(row(name=""), V)[0] == "invalid"


def test_cross_validate_dpiit_against_mca() -> None:
    d = RegistryRow(source="dpiit", source_id="DIPP1", name="Acme", cin=EDU_CIN, state="Karnataka",
                    aliases=["Acme Learning Private Limited"])
    assert cross_validate(d, row(), V) == ["ok:cross_validated_with_mca"]
    other = row(name="Zeta Logistics Private Limited", state="Maharashtra")
    issues = cross_validate(d, other, V)
    assert "warning:name_mismatch_with_mca" in issues and "warning:state_mismatch_with_mca" in issues
    assert cross_validate(d, None, V) == ["warning:cin_not_in_mca"]


def test_registry_name_vocabulary() -> None:
    from screener.universe.records import name_is_educational

    edu = U["education"]
    for name in ("Acme Learning Private Limited", "Brightpath Academy LLP", "EduNova Technologies", "Vidyakosh Pvt Ltd",
                 "SkillBridge Ventures", "Sharma Coaching Classes"):
        assert name_is_educational(name, edu, PF), name
    for name in ("Zeta Logistics Private Limited", "Reduce Waste Pvt Ltd", "Machine Works Ltd"):
        assert not name_is_educational(name, edu, PF), name


def test_status_class() -> None:
    assert status_class("Active", V) == "active"
    assert status_class("Strike Off", V) == "inactive"
    assert status_class("Under Process of Striking Off", V) == "inactive"
    assert status_class("", V) == "unknown"


# --- triage ------------------------------------------------------------------------------------------


def test_triage_education_nic_and_name() -> None:
    t = triage([row()], [], U, PF, TODAY)
    assert t.crawl and "education NIC code in CIN" in t.reasons and "education keyword in name" in t.reasons


def test_triage_it_registered_edtech_needs_other_signals() -> None:
    it = row(cin=IT_CIN, source_id=IT_CIN, nic_code="72900", name="Sorting Hat Technologies Private Limited")
    assert not triage([it], [], U, PF, TODAY).crawl  # IT code, no keyword: below threshold
    dpiit = RegistryRow(source="dpiit", source_id="D1", name="Sorting Hat", cin=IT_CIN, sector="Education | EdTech")
    t = triage([it, dpiit], ["ok:cross_validated_with_mca"], U, PF, TODAY)
    assert t.crawl and "DPIIT sector is education" in t.reasons


def test_triage_never_crawls_inactive_or_invalid() -> None:
    assert not triage([row(status="Strike Off")], [], U, PF, TODAY).crawl
    assert not triage([row()], ["invalid:cin_format"], U, PF, TODAY).crawl


def test_merge_key() -> None:
    assert merge_key(row(), V["cin_pattern"]) == f"cin:{EDU_CIN}"
    # names are not suffix-stripped for merge keys (exact merges only; fuzzy matching is the resolver's job)
    assert merge_key(RegistryRow(source="scout", source_id="x", name="Acme Learning"), V["cin_pattern"]) == "name:acme learning|"


# --- store, prospects, queue (SQLite) ----------------------------------------------------------------


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def count(s: Session, model) -> int:
    return s.scalar(select(func.count()).select_from(model))


def test_upsert_is_idempotent(session: Session) -> None:
    rows = [row(), row(source_id=OLD_EDU_CIN, cin=OLD_EDU_CIN, nic_code="80903", name="Old School Pvt Ltd",
                       registered_on=date(2011, 5, 1))]
    upsert_rows(session, rows, V)
    upsert_rows(session, rows, V)
    session.commit()
    assert count(session, RegistryEntity) == 2


def test_build_prospects_queues_once_and_requeues_only_on_change(session: Session) -> None:
    upsert_rows(session, [row(), row(source_id=IT_CIN, cin=IT_CIN, nic_code="72900", name="Logistics Co Pvt Ltd")], V)
    session.commit()
    first = build_prospects(session, U, PF)
    assert first.get("queued") == 1 and first.get("triaged_out") == 1
    assert count(session, Task) == 1
    again = build_prospects(session, U, PF)
    assert again == {"unchanged": 2} and count(session, Task) == 1
    upsert_rows(session, [row(status="Strike Off")], V)
    session.commit()
    changed = build_prospects(session, U, PF)
    assert changed.get("changed") == 1 and changed.get("triaged_out") == 1


def test_dpiit_csv_and_cross_validation(session: Session, tmp_path: Path) -> None:
    upsert_rows(session, [row()], V)
    session.commit()
    csv_path = tmp_path / "dpiit.csv"
    csv_path.write_text(
        "﻿State,City,Company Name,Legal Name,Cin,Company Website,Company Status,Focus Industry,Focus Sector,Services Provided\n"
        f"Karnataka,Bengaluru,Acme,Acme Learning Private Limited,{EDU_CIN},acme.in,Active,Education,EdTech,Tutoring\n",
        encoding="utf-8")
    assert load_dpiit_csv(session, csv_path, U) == 1
    assert cross_validate_all(session, V) == {"ok:cross_validated_with_mca": 1}
    build_prospects(session, U, PF)
    p = session.scalar(select(Prospect))
    assert p.merge_key == f"cin:{EDU_CIN}" and p.sources == ["dpiit", "mca"] and p.domain == "acme.in"
    assert "DPIIT record cross-validated with MCA" in p.triage_reasons


def test_stored_verdict_is_reused_never_paid_twice(session: Session) -> None:
    p = Prospect(id=uuid.uuid4(), merge_key="cin:x", name="Acme", content_hash="h1")
    session.add(p)
    record(session, p, LightOutcome("not_fit", "payments company", llm_calls=1), refresh_days=180)
    assert already_classified(p)
    assert not already_classified(p, now=datetime.now(timezone.utc) + timedelta(days=181))  # refresh due
    p.content_hash = "h2"
    assert not already_classified(p)  # registry data changed


def test_queue_claim_finish_and_backoff(session: Session) -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    enqueue(session, "light", a, 5.0)
    enqueue(session, "light", b, 9.0)
    enqueue(session, "light", a, 5.0)  # duplicate: still one task
    session.commit()
    assert count(session, Task) == 2
    claimed = claim(session, ["light"], 1, 900)
    assert [t.subject_id for t in claimed] == [b]  # highest priority first
    assert claim(session, ["light"], 5, 900)[0].subject_id == a  # b is locked
    finish(session, claimed[0], {"verdict": "fit"})
    enqueue(session, "light", b, 9.0)
    session.commit()
    assert session.get(Task, claimed[0].id).status == "done"  # done tasks are not re-queued
    t = session.scalar(select(Task).where(Task.subject_id == a))
    fail(session, t, "boom", max_attempts=3)
    assert t.status == "queued" and t.run_after.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    t.attempts = 3
    fail(session, t, "boom", max_attempts=3)
    assert t.status == "failed"
