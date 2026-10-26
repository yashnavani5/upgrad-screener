"""v_search: one searchable row per company, founder and registry entry (MCA / DPIIT), for the dashboard's
search and "ask" box. Trigram indexes keep substring search fast over ~1.5 lakh registry rows.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

V_SEARCH = r"""
CREATE OR REPLACE VIEW v_search AS
WITH latest AS (
    SELECT cand.company_id, s.shortlist_rank, s.rank, s.fit_type, s.final_prime,
           COALESCE(s.features->>'geo', 'unknown') AS region
    FROM scores s JOIN candidates cand ON cand.id = s.candidate_id
    WHERE s.run_id = (SELECT id FROM latest_published_run)
)
SELECT
    'company'::text AS kind,
    c.id AS entity_id,
    c.id AS company_id,
    c.name,
    NULL::text AS company,
    c.website,
    c.description,
    NULLIF(concat_ws(', ', c.city, c.country), '') AS location,
    COALESCE(l.region, 'unknown') AS region,
    l.shortlist_rank,
    l.rank,
    l.fit_type,
    l.final_prime AS final,
    c.status,
    NULL::text AS cin,
    NULL::date AS registered_on,
    NULL::double precision AS paid_up_capital_inr,
    NULL::double precision AS authorized_capital_inr,
    c.total_raised_usd
FROM companies c LEFT JOIN latest l ON l.company_id = c.id
UNION ALL
SELECT
    'founder', fo.id, c.id, fo.full_name, c.name, c.website, COALESCE(fo.current_title, cf.role),
    NULLIF(concat_ws(', ', c.city, c.country), ''), COALESCE(l.region, 'unknown'), l.shortlist_rank, l.rank,
    l.fit_type, l.final_prime, c.status, NULL, NULL, NULL, NULL, NULL
FROM founders fo
JOIN company_founders cf ON cf.founder_id = fo.id
JOIN companies c ON c.id = cf.company_id
LEFT JOIN latest l ON l.company_id = c.id
UNION ALL
SELECT
    'registry', re.id, NULL, re.name, NULL, re.website, COALESCE(re.activity, re.sector),
    NULLIF(concat_ws(', ', re.city, re.state), ''), 'india', NULL, NULL, NULL, NULL, re.status, re.cin,
    re.registered_on, re.paid_up_capital_inr,
    CASE WHEN (re.raw->>'AuthorizedCapital') ~ '^[0-9]+(\.[0-9]+)?$' THEN (re.raw->>'AuthorizedCapital')::double precision END,
    NULL
FROM registry_entities re
WHERE re.source IN ('mca', 'dpiit')
"""

GRANT = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'web_ro') THEN
        EXECUTE 'GRANT SELECT ON v_search TO web_ro';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registry_name_trgm ON registry_entities USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_companies_name_trgm ON companies USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_founders_name_trgm ON founders USING gin (full_name gin_trgm_ops)")
    op.execute(V_SEARCH)
    op.execute(GRANT)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_search")
    op.execute("DROP INDEX IF EXISTS ix_founders_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_companies_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_registry_name_trgm")
