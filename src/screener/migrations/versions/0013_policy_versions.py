"""AI Policy: policy_versions table and the v_policy view (all versions, newest first; one is active)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

V_POLICY = """
CREATE OR REPLACE VIEW v_policy AS
SELECT
    p.version,
    p.status,
    p.author,
    p.note,
    p.policy,
    p.changes,
    p.created_at,
    (SELECT count(*)::int FROM runs r WHERE (r.config_snapshot->'weights'->'policy'->>'version')::int = p.version) AS runs_scored
FROM policy_versions p
"""

GRANT = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') THEN
        EXECUTE 'GRANT SELECT ON v_policy TO web_ro';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        "policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="draft"),
        sa.Column("author", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("policy", JSON, nullable=False),
        sa.Column("changes", JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute(V_POLICY)
    op.execute(GRANT)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_policy")
    op.drop_table("policy_versions")
