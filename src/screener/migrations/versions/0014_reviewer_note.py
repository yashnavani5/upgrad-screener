"""Reviewer note and reviewer (who wrote it) on v_shortlist, next to the existing reviewer_label.

The note is the reviewer's view (Sheet "Reviewer note" or config/reviewer_picks.yaml), never a verified fact; the
dashboard shows it labelled as such. The view text is 0011's v_shortlist with two columns appended, so
CREATE OR REPLACE VIEW works without a drop and grants are kept.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-26
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TAIL = "    c.description\nFROM scores s"
REVIEWER = """    c.description,
    (SELECT fb.note FROM feedback fb WHERE fb.entity_id = c.id ORDER BY fb.synced_at DESC LIMIT 1) AS reviewer_note,
    (SELECT fb.reviewer FROM feedback fb WHERE fb.entity_id = c.id ORDER BY fb.synced_at DESC LIMIT 1) AS reviewer
FROM scores s"""


def _v0011() -> str:
    path = Path(__file__).with_name("0011_founder_profiles_curated.py")
    spec = importlib.util.spec_from_file_location("_m0011", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.VIEWS["v_shortlist"]


def upgrade() -> None:
    sql = _v0011()
    assert sql.count(TAIL) == 1, "v_shortlist text changed; update 0014"
    op.execute(f"CREATE OR REPLACE VIEW v_shortlist AS {sql.replace(TAIL, REVIEWER)}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_shortlist")
    op.execute(f"CREATE VIEW v_shortlist AS {_v0011()}")
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') THEN
        EXECUTE 'GRANT SELECT ON v_shortlist TO web_ro';
    END IF;
END $$;
""")
