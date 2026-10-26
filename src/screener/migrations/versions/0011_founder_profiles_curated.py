"""Founder profiles (title, X link, verified background, one-line bio) in v_shortlist / v_company_detail;
curated "left out" picks (featured, category, line) on exclusions and v_left_out; company description on
v_shortlist and v_left_out.

Founder JSON keeps its existing keys and adds new ones (same column type), and new columns are appended, so
CREATE OR REPLACE VIEW works without a drop.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

VIEWS: dict[str, str] = {
    "v_shortlist": r"""
SELECT
    s.run_id,
    ((s.run_id) = (SELECT id FROM latest_published_run)) IS TRUE AS is_latest,
    s.shortlist_rank AS rank,
    c.id AS entity_id,
    c.name AS company,
    c.website,
    (
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'name', fo.full_name, 'linkedin_url', fo.linkedin_url, 'role', cf.role,
               'founder_id', fo.id, 'title', fo.current_title, 'identity_verified', fo.identity_verified,
               'x_url', (
                   SELECT u FROM jsonb_array_elements_text(COALESCE(fo.other_urls, '[]'::jsonb)) u
                   WHERE u ~* '^https?://(www\.)?(x|twitter)\.com/' LIMIT 1),
               'background', (
                   SELECT COALESCE(jsonb_agg(jsonb_build_object('field', fc.field, 'value', fc.value_json,
                                                                 'source_url', fc.source_url)
                                    ORDER BY fc.field, fc.retrieved_at DESC), '[]'::jsonb)
                   FROM claims fc
                   WHERE fc.entity_type = 'founder' AND fc.entity_id = fo.id AND fc.verified_status = 'supported'
                     AND fc.field IN ('prior_role', 'prior_exit', 'education')),
               'line', s.features->'founder_lines'->(fo.id::text)->>'line')
             ORDER BY fo.full_name), '[]'::jsonb)
    FROM company_founders cf JOIN founders fo ON fo.id = cf.founder_id
    WHERE cf.company_id = c.id AND cf.is_current
) AS founders,
    s.fit_type,
    s.final_prime AS final,
    s.t,
    s.i,
    s.f,
    s.c AS confidence,
    COALESCE(s.why_line, '') AS why_line,
    COALESCE(s.fit_line, '') AS fit_line,
    COALESCE(s.evidence, '[]'::jsonb) AS evidence,
    (SELECT max(cl.retrieved_at) 
    FROM claims cl
    JOIN company_claim_entities ce ON ce.entity_type = cl.entity_type AND ce.entity_id = cl.entity_id
    WHERE ce.company_id = c.id AND cl.verified_status = 'supported'
) AS last_verified,
    (SELECT fb.label FROM feedback fb WHERE fb.entity_id = c.id ORDER BY fb.synced_at DESC LIMIT 1) AS reviewer_label,
    COALESCE(s.features->>'geo', 'unknown') AS region,
    COALESCE(s.features->>'geo_basis', 'no location evidence') AS region_basis,
    COALESCE((s.features->>'size_known')::boolean, false) AS size_verified,
    c.description
FROM scores s
JOIN candidates cand ON cand.id = s.candidate_id
JOIN companies c ON c.id = cand.company_id
WHERE s.shortlist_rank IS NOT NULL
""",
    "v_company_detail": r"""
SELECT
    cand.run_id,
    ((cand.run_id) = (SELECT id FROM latest_published_run)) IS TRUE AS is_latest,
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
                   'role', cf.role, 'is_current', cf.is_current,
                   'title', fo.current_title, 'identity_verified', fo.identity_verified,
                   'x_url', (
                   SELECT u FROM jsonb_array_elements_text(COALESCE(fo.other_urls, '[]'::jsonb)) u
                   WHERE u ~* '^https?://(www\.)?(x|twitter)\.com/' LIMIT 1),
                   'background', (
                   SELECT COALESCE(jsonb_agg(jsonb_build_object('field', fc.field, 'value', fc.value_json,
                                                                 'source_url', fc.source_url)
                                    ORDER BY fc.field, fc.retrieved_at DESC), '[]'::jsonb)
                   FROM claims fc
                   WHERE fc.entity_type = 'founder' AND fc.entity_id = fo.id AND fc.verified_status = 'supported'
                     AND fc.field IN ('prior_role', 'prior_exit', 'education')),
                   'line', s.features->'founder_lines'->(fo.id::text)->>'line')
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
        
    FROM claims cl
    JOIN company_claim_entities ce ON ce.entity_type = cl.entity_type AND ce.entity_id = cl.entity_id
    WHERE ce.company_id = c.id AND cl.verified_status = 'supported'

    ) AS claims,
    COALESCE(s.features->>'geo', 'unknown') AS region,
    COALESCE(s.features->>'geo_basis', 'no location evidence') AS region_basis
FROM candidates cand
JOIN companies c ON c.id = cand.company_id
LEFT JOIN scores s ON s.candidate_id = cand.id
""",
    "v_left_out": r"""
SELECT
    e.run_id,
    ((e.run_id) = (SELECT id FROM latest_published_run)) IS TRUE AS is_latest,
    c.id AS entity_id,
    c.name AS company,
    (
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'name', fo.full_name, 'linkedin_url', fo.linkedin_url, 'role', cf.role)
             ORDER BY fo.full_name), '[]'::jsonb)
    FROM company_founders cf JOIN founders fo ON fo.id = cf.founder_id
    WHERE cf.company_id = c.id AND cf.is_current
) AS founders,
    e.reason_code,
    e.explanation,
    e.would_have_ranked,
    src.source_url,
    COALESCE((SELECT sc.features->>'geo' FROM scores sc WHERE sc.candidate_id = cand.id LIMIT 1), 'unknown') AS region,
    e.featured,
    e.featured_category,
    e.featured_line,
    c.description
FROM exclusions e
JOIN candidates cand ON cand.id = e.candidate_id
JOIN companies c ON c.id = cand.company_id
LEFT JOIN claims src ON src.id = e.source_claim_id
""",
}


def upgrade() -> None:
    op.add_column("exclusions", sa.Column("featured", sa.Integer(), nullable=True))
    op.add_column("exclusions", sa.Column("featured_category", sa.String(length=20), nullable=True))
    op.add_column("exclusions", sa.Column("featured_line", sa.Text(), nullable=True))
    for name, sql in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW {name} AS {sql}")


def downgrade() -> None:
    # The view changes stay (they only add keys/columns); harmless.
    op.drop_column("exclusions", "featured_line")
    op.drop_column("exclusions", "featured_category")
    op.drop_column("exclusions", "featured")
