"""v_* views on a real Postgres match src/screener/contracts.py. Runs only with TEST_DATABASE_URL set."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from screener.contracts import VIEW_CONTRACTS
from screener.db import make_engine

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def engine():
    return make_engine(os.environ["TEST_DATABASE_URL"])


@pytest.mark.parametrize("view", sorted(VIEW_CONTRACTS))
def test_view_columns_match_contract(engine, view: str) -> None:
    with engine.connect() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:v"),
                {"v": view},
            )
        }
    assert cols, f"{view} does not exist; run `screener db init`"
    assert cols == set(VIEW_CONTRACTS[view].model_fields)


@pytest.mark.parametrize("view", sorted(VIEW_CONTRACTS))
def test_view_is_queryable(engine, view: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"SELECT * FROM {view} LIMIT 1")).all()


def test_web_ro_can_read_views_only(engine) -> None:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.role_table_grants WHERE grantee='web_ro' AND privilege_type='SELECT'")
        ).all()
    granted = {r[0] for r in rows}
    assert granted == set(VIEW_CONTRACTS)
