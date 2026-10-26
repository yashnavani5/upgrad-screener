"""dashboard views (v_*) and web_ro grants

The v_* views are the only thing the frontend reads (docs/ARCHITECTURE.md section 4). Row shapes must
match src/screener/contracts.py; tests/test_views_pg.py checks column parity against a real Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PUBLIC_VIEWS = ("v_shortlist", "v_company_detail", "v_left_out", "v_long_list", "v_runs", "v_funnel")
INTERNAL_VIEWS = ("latest_published_run", "company_claim_entities")

IS_LATEST = "(({col}) = (SELECT id FROM latest_published_run)) IS TRUE"

FOUNDERS_JSON = """(
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'name', fo.full_name, 'linkedin_url', fo.linkedin_url, 'role', cf.role)
             ORDER BY fo.full_name), '[]'::jsonb)
    FROM company_founders cf JOIN founders fo ON fo.id = cf.founder_id
    WHERE cf.company_id = c.id AND cf.is_current
)"""

SUPPORTED_CLAIMS_OF_COMPANY = """
    FROM claims cl
    JOIN company_claim_entities ce ON ce.entity_type = cl.entity_type AND ce.entity_id = cl.entity_id
    WHERE ce.company_id = c.id AND cl.verified_status = 'supported'
"""

VIEWS: dict[str, str] = {
    # Latest run that completed Publish (succeeded or partial).
    "latest_published_run": """
SELECT id FROM runs
WHERE status IN ('succeeded', 'partial')
ORDER BY started_at DESC
LIMIT 1
""",
    # Every entity whose claims belong to a company: the company itself, its founders, its rounds.
    "company_claim_entities": """
SELECT id AS company_id, 'company'::varchar AS entity_type, id AS entity_id FROM companies
UNION ALL
SELECT company_id, 'founder'::varchar, founder_id FROM company_founders
UNION ALL
SELECT company_id, 'round'::varchar, id FROM rounds
""",
    "v_shortlist": f"""
SELECT
    s.run_id,
    {IS_LATEST.format(col="s.run_id")} AS is_latest,
    s.shortlist_rank AS rank,
    c.id AS entity_id,
    c.name AS company,
    c.website,
    {FOUNDERS_JSON} AS founders,
    s.fit_type,
    s.final_prime AS final,
    s.t,
    s.i,
    s.f,
    s.c AS confidence,
    COALESCE(s.why_line, '') AS why_line,
    COALESCE(s.fit_line, '') AS fit_line,
    COALESCE(s.evidence, '[]'::jsonb) AS evidence,
    (SELECT max(cl.retrieved_at) {SUPPORTED_CLAIMS_OF_COMPANY}) AS last_verified,
    (SELECT fb.label FROM feedback fb WHERE fb.entity_id = c.id ORDER BY fb.synced_at DESC LIMIT 1) AS reviewer_label
FROM scores s
JOIN candidates cand ON cand.id = s.candidate_id
JOIN companies c ON c.id = cand.company_id
WHERE s.shortlist_rank IS NOT NULL
""",
    "v_company_detail": f"""
SELECT
    cand.run_id,
    {IS_LATEST.format(col="cand.run_id")} AS is_latest,
    c.id AS entity_id,
    c.name AS company,
    c.website,
    c.description,
    c.country,
    c.city,
    c.founded_year,
    c.status,
    c.total_raised_usd,
    c.headcount,
    s.fit_type,
    s.shortlist_rank AS rank,
    (
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'founder_id', fo.id, 'name', fo.full_name, 'linkedin_url', fo.linkedin_url,
                   'role', cf.role, 'is_current', cf.is_current)
                 ORDER BY cf.is_current DESC, fo.full_name), '[]'::jsonb)
        FROM company_founders cf JOIN founders fo ON fo.id = cf.founder_id
        WHERE cf.company_id = c.id
    ) AS founders,
    (
        -- Inner join on the source claim: a round without a source does not exist.
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'round_type', r.round_type, 'amount_usd', r.amount_usd, 'announced_on', r.announced_on,
                   'investors', r.investors, 'source_url', rc.source_url)
                 ORDER BY r.announced_on NULLS LAST), '[]'::jsonb)
        FROM rounds r JOIN claims rc ON rc.id = r.source_claim_id
        WHERE r.company_id = c.id
    ) AS rounds,
    COALESCE(s.breakdown, '[]'::jsonb) AS score_breakdown,
    (
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'field', cl.field, 'value', cl.value_json, 'source_url', cl.source_url,
                   'evidence_span', cl.evidence_span, 'retrieved_at', cl.retrieved_at,
                   'verifier_confidence', cl.verifier_confidence)
                 ORDER BY cl.retrieved_at DESC), '[]'::jsonb)
        {SUPPORTED_CLAIMS_OF_COMPANY}
    ) AS claims
FROM candidates cand
JOIN companies c ON c.id = cand.company_id
LEFT JOIN scores s ON s.candidate_id = cand.id
""",
    "v_left_out": f"""
SELECT
    e.run_id,
    {IS_LATEST.format(col="e.run_id")} AS is_latest,
    c.id AS entity_id,
    c.name AS company,
    {FOUNDERS_JSON} AS founders,
    e.reason_code,
    e.explanation,
    e.would_have_ranked,
    src.source_url
FROM exclusions e
JOIN candidates cand ON cand.id = e.candidate_id
JOIN companies c ON c.id = cand.company_id
LEFT JOIN claims src ON src.id = e.source_claim_id
""",
    "v_long_list": f"""
SELECT
    s.run_id,
    {IS_LATEST.format(col="s.run_id")} AS is_latest,
    s.rank,
    c.id AS entity_id,
    c.name AS company,
    c.website,
    {FOUNDERS_JSON} AS founders,
    s.fit_type,
    s.final_prime AS final,
    s.t,
    s.m,
    s.n,
    s.i,
    s.f,
    s.f_acquire,
    s.f_partner,
    s.f_co_build,
    COALESCE(s.c, 0.0) AS confidence,
    s.bt_pct,
    COALESCE(cand.gate_status, 'failed') AS gate_status,
    (SELECT e.reason_code FROM exclusions e WHERE e.candidate_id = cand.id ORDER BY e.created_at LIMIT 1) AS reason_code
FROM scores s
JOIN candidates cand ON cand.id = s.candidate_id
JOIN companies c ON c.id = cand.company_id
WHERE s.rank IS NOT NULL AND s.rank <= 200 AND s.final_prime IS NOT NULL
""",
    "v_runs": f"""
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
    {IS_LATEST.format(col="r.id")} AS is_latest
FROM r
""",
    "v_funnel": f"""
SELECT
    f.run_id,
    {IS_LATEST.format(col="f.run_id")} AS is_latest,
    f.stage,
    f.stage_order,
    f.count
FROM run_funnel f
""",
}

GRANT_WEB_RO = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') THEN
        EXECUTE 'GRANT SELECT ON {views} TO web_ro';
    END IF;
END
$$;
"""


def upgrade() -> None:
    for name in (*INTERNAL_VIEWS, *PUBLIC_VIEWS):
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {VIEWS[name]}")
    op.execute(GRANT_WEB_RO.format(views=", ".join(PUBLIC_VIEWS)))


def downgrade() -> None:
    for name in (*PUBLIC_VIEWS, *INTERNAL_VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
