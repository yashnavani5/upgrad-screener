"""live dashboard views: v_runs gains checkpoints/options; v_activity and v_universe added

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

V_RUNS = """
CREATE OR REPLACE VIEW v_runs AS
WITH r AS (
    SELECT runs.*,
           (status IN ('queued', 'running')
            AND COALESCE(heartbeat_at, started_at) < now() - interval '15 minutes') AS stale
    FROM runs
)
SELECT
    r.id AS run_id,
    CASE WHEN r.stale THEN 'failed' ELSE r.status END AS status,
    r.current_stage,
    r.stage_progress,
    r.trigger,
    r.started_at,
    r.finished_at,
    r.heartbeat_at,
    r.counts,
    r.provider_usage,
    r.cost_usd,
    CASE WHEN r.stale THEN COALESCE(r.error_summary, 'stale: no heartbeat') ELSE r.error_summary END AS error_summary,
    r.config_snapshot,
    r.sheet_url,
    ((r.id) = (SELECT id FROM latest_published_run)) IS TRUE AS is_latest,
    r.checkpoints,
    r.options
FROM r
"""

V_ACTIVITY = """
CREATE OR REPLACE VIEW v_activity AS
SELECT
    provider,
    count(*) FILTER (WHERE created_at > now() - interval '5 minutes') AS calls_5m,
    count(*) FILTER (WHERE created_at > now() - interval '5 minutes' AND status NOT IN ('ok', 'blocked')) AS errors_5m,
    count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS calls_1h,
    (percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
        FILTER (WHERE created_at > now() - interval '1 hour'))::int AS median_latency_ms_1h,
    max(created_at) AS last_call_at
FROM api_calls
WHERE created_at > now() - interval '1 hour'
GROUP BY provider
"""

V_UNIVERSE = """
CREATE OR REPLACE VIEW v_universe AS
SELECT 'registry' AS area, source || ' / ' || validation_status AS bucket, count(*)::int AS count
FROM registry_entities GROUP BY source, validation_status
UNION ALL
SELECT 'prospects', stage || ' / ' || COALESCE(verdict, '-'), count(*)::int FROM prospects GROUP BY stage, verdict
UNION ALL
SELECT 'tasks', kind || ' / ' || status, count(*)::int FROM tasks GROUP BY kind, status
UNION ALL
SELECT 'claims', verified_status, count(*)::int FROM claims GROUP BY verified_status
UNION ALL
SELECT 'companies', 'total', count(*)::int FROM companies
UNION ALL
SELECT 'companies', 'enriched', count(*)::int FROM companies WHERE enriched_at IS NOT NULL
UNION ALL
SELECT 'companies', 'verified', count(*)::int FROM companies WHERE verified_at IS NOT NULL
UNION ALL
SELECT 'founders', 'total', count(*)::int FROM founders
UNION ALL
SELECT 'founders', 'identity verified', count(*)::int FROM founders WHERE identity_verified
"""

GRANT = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') THEN
        EXECUTE 'GRANT SELECT ON v_runs, v_activity, v_universe TO web_ro';
    END IF;
END
$$;
"""


def upgrade() -> None:
    # Column order changes need a drop/create (CREATE OR REPLACE can only append columns).
    op.execute("DROP VIEW IF EXISTS v_runs")
    op.execute(V_RUNS)
    op.execute(V_ACTIVITY)
    op.execute(V_UNIVERSE)
    op.execute(GRANT)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_universe")
    op.execute("DROP VIEW IF EXISTS v_activity")
