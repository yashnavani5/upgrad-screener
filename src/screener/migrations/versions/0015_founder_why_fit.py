"""Founder "why they made the cut" and "how they fit upGrad" lines (explain stage) in the founders JSON of
v_shortlist and v_company_detail, next to the existing one-line intro.

Same keys plus two new ones, so CREATE OR REPLACE VIEW works without a drop. View texts are 0011's (with 0014's
reviewer columns for v_shortlist) with the founder object extended.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-27
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

LINE = "'line', s.features->'founder_lines'->(fo.id::text)->>'line')"
WHY_FIT = ("'line', s.features->'founder_lines'->(fo.id::text)->>'line',\n"
           "               'why', s.features->'founder_lines'->(fo.id::text)->>'why',\n"
           "               'fit', s.features->'founder_lines'->(fo.id::text)->>'fit')")


def _load(name: str):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_m_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def views() -> dict[str, str]:
    m11, m14 = _load("0011_founder_profiles_curated.py"), _load("0014_reviewer_note.py")
    return {"v_shortlist": m11.VIEWS["v_shortlist"].replace(m14.TAIL, m14.REVIEWER),
            "v_company_detail": m11.VIEWS["v_company_detail"]}


def upgrade() -> None:
    for name, sql in views().items():
        assert sql.count(LINE) == 1, f"{name} text changed; update 0015"
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {sql.replace(LINE, WHY_FIT)}")


def downgrade() -> None:
    pass  # the extra JSON keys are harmless; 0014's downgrade restores v_shortlist if needed
