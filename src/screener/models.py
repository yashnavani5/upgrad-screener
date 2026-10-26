"""SQLAlchemy 2 models for every table in docs/SPEC.md section 2.

JSON columns are JSON on SQLite and JSONB on Postgres. Embeddings are pgvector `vector` (unconstrained
dimension, model recorded alongside) on Postgres and JSON on SQLite.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")
EmbeddingType = JSON().with_variant(Vector(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Company(Timestamped, Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(300), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    founded_year: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    total_raised_usd: Mapped[float | None] = mapped_column(Float)
    headcount: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(EmbeddingType)
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    # Materialized from supported claims by `screener verify` (claims remain the source of truth).
    legal_name: Mapped[str | None] = mapped_column(String(300))
    segment_tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    capability_tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    open_roles: Mapped[int | None] = mapped_column(Integer)
    last_activity_at: Mapped[date | None] = mapped_column(Date)
    status_reason: Mapped[str | None] = mapped_column(Text)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Targeted lookups already done (enrich/lookups.py): {"size": iso date, "location": iso date}. Never paid twice.
    lookups: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class Founder(Timestamped, Base):
    __tablename__ = "founders"

    id: Mapped[uuid.UUID] = uuid_pk()
    full_name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str | None] = mapped_column(String(300), index=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, index=True)
    other_urls: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    current_title: Mapped[str | None] = mapped_column(String(300))
    # Founder name + company linked by >= 1 supported claim from a non-LinkedIn source.
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Targeted lookups already done (enrich/lookups.py): {"background": iso date}.
    lookups: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class CompanyFounder(Timestamped, Base):
    __tablename__ = "company_founders"
    __table_args__ = (UniqueConstraint("company_id", "founder_id", name="uq_company_founder"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    founder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("founders.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str | None] = mapped_column(String(200))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)


class Claim(Timestamped, Base):
    __tablename__ = "claims"
    __table_args__ = (Index("ix_claims_entity", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # company | founder | round
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    value_json: Mapped[Any | None] = mapped_column(JSONType)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_span: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_model: Mapped[str | None] = mapped_column(String(200))
    extract_confidence: Mapped[float | None] = mapped_column(Float)
    verified_status: Mapped[str] = mapped_column(String(20), default="unverified", nullable=False)
    verifier_confidence: Mapped[float | None] = mapped_column(Float)
    verifier_reason: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of (entity, field, value, source_url): re-running enrichment never duplicates a claim.
    claim_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    # grounding score, extraction context, corroboration {needed, corroborated, domains, primary}.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class Round(Timestamped, Base):
    __tablename__ = "rounds"

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    round_type: Mapped[str | None] = mapped_column(String(50))
    amount_usd: Mapped[float | None] = mapped_column(Float)
    announced_on: Mapped[date | None] = mapped_column(Date)
    investors: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))


class Run(Timestamped, Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    trigger: Mapped[str] = mapped_column(String(20), default="cli", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(20))
    stage_progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(40))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    provider_usage: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    counts: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    sheet_url: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    # Resumable state machine: options the run was started with, and per-stage checkpoints
    # {stage: {status: done|partial|skipped|failed|running, started_at, finished_at, summary}}.
    options: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    checkpoints: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)


class Candidate(Timestamped, Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("run_id", "company_id", name="uq_candidate_run_company"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    founder_ids: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), default="discovered", nullable=False)
    gate_status: Mapped[str | None] = mapped_column(String(10))  # passed | failed


class Score(Timestamped, Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("candidate_id", name="uq_score_candidate"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    t: Mapped[float | None] = mapped_column(Float)
    m: Mapped[float | None] = mapped_column(Float)
    n: Mapped[float | None] = mapped_column(Float)
    i: Mapped[float | None] = mapped_column(Float)
    f: Mapped[float | None] = mapped_column(Float)
    f_acquire: Mapped[float | None] = mapped_column(Float)
    f_partner: Mapped[float | None] = mapped_column(Float)
    f_co_build: Mapped[float | None] = mapped_column(Float)
    fit_type: Mapped[str | None] = mapped_column(String(20))
    c: Mapped[float | None] = mapped_column(Float)
    final: Mapped[float | None] = mapped_column(Float)
    bt_score: Mapped[float | None] = mapped_column(Float)
    bt_pct: Mapped[float | None] = mapped_column(Float)
    final_prime: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)  # rank by final_prime within the run
    shortlist_rank: Mapped[int | None] = mapped_column(Integer)  # MMR order; null = not shortlisted
    features: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    breakdown: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    why_line: Mapped[str | None] = mapped_column(Text)
    fit_line: Mapped[str | None] = mapped_column(Text)
    weights_version: Mapped[str | None] = mapped_column(String(40))


class Exclusion(Timestamped, Base):
    __tablename__ = "exclusions"

    id: Mapped[uuid.UUID] = uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    would_have_ranked: Mapped[int | None] = mapped_column(Integer)
    source_claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id", ondelete="SET NULL"))
    evidence_claim_ids: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    # Curated "left out" picks (explain/people.py): 1..3 in display order, the category and a cited line.
    featured: Mapped[int | None] = mapped_column(Integer)
    featured_category: Mapped[str | None] = mapped_column(String(20))
    featured_line: Mapped[str | None] = mapped_column(Text)


class Feedback(Timestamped, Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    reviewer: Mapped[str | None] = mapped_column(String(200))
    label: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RunFunnel(Base):
    __tablename__ = "run_funnel"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(20), primary_key=True)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ApiCall(Base):
    __tablename__ = "api_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # ok | http_<code> | error | blocked
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Discovery(Base):
    """Raw scout output, before entity resolution. Scouts cast a wide net; quality is decided later."""

    __tablename__ = "discoveries"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_discovery_dedupe"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    scout: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    raw_company_name: Mapped[str] = mapped_column(String(300), nullable=False)
    raw_person_name: Mapped[str | None] = mapped_column(String(300))
    company_url: Mapped[str | None] = mapped_column(Text)
    person_url: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)
    evidence_span: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # Set by `screener resolve`: the canonical entities this candidate was resolved to.
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    founder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("founders.id", ondelete="SET NULL"), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyAlias(Base):
    """Every raw name a company was seen under (for search, review and future matching)."""

    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("company_id", "alias", name="uq_company_alias"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ResolutionPair(Base):
    """Audit trail of pairwise merge decisions (rule or LLM tiebreak), keyed by the two records' identities."""

    __tablename__ = "resolution_pairs"
    __table_args__ = (UniqueConstraint("pair_key", name="uq_resolution_pair"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    pair_key: Mapped[str] = mapped_column(String(64), nullable=False)
    left_name: Mapped[str] = mapped_column(String(300), nullable=False)
    right_name: Mapped[str] = mapped_column(String(300), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # merge | separate
    method: Mapped[str] = mapped_column(String(30), nullable=False)    # domain | rule | llm | domain_conflict | ...
    reason: Mapped[str | None] = mapped_column(Text)
    features: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Judgment(Base):
    """Pairwise judge decisions (both orders) for a scoring run: audit trail for the BT blend."""

    __tablename__ = "judgments"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    company_a: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    company_b: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    winner_ab: Mapped[str | None] = mapped_column(String(1))   # judgment with A shown first
    winner_ba: Mapped[str | None] = mapped_column(String(1))   # judgment with B shown first
    outcome: Mapped[uuid.UUID | None] = mapped_column(Uuid)     # winning company, or null for a tie
    reason_ab: Mapped[str | None] = mapped_column(Text)
    reason_ba: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RegistryEntity(Base):
    """One row from a bulk registry (MCA company master, DPIIT startups, ...). Lakhs of rows; no LLM involved."""

    __tablename__ = "registry_entities"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_registry_source_id"),
        Index("ix_registry_cin", "cin"),
        Index("ix_registry_norm_state", "normalized_name", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(String(20), nullable=False)          # mca | dpiit | scout
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)      # CIN / recognition id / discovery id
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(400), nullable=False)
    cin: Mapped[str | None] = mapped_column(String(30))
    nic_code: Mapped[str | None] = mapped_column(String(5))                  # from the CIN (chars 2-6)
    status: Mapped[str | None] = mapped_column(String(80))
    registered_on: Mapped[date | None] = mapped_column(Date)
    paid_up_capital_inr: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(120))
    activity: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(12), default="unchecked", nullable=False)  # valid | warning | invalid
    validation_issues: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    cross_ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)              # matching row in another source
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Prospect(Base):
    """One real-world company in the universe (merged across registries by CIN, else name + state).
    Holds the triage score and the stored light-tier verdict, so nothing is crawled or classified twice."""

    __tablename__ = "prospects"
    __table_args__ = (
        UniqueConstraint("merge_key", name="uq_prospect_merge_key"),
        Index("ix_prospect_stage_score", "stage", "triage_score"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    merge_key: Mapped[str] = mapped_column(String(200), nullable=False)      # cin:<CIN> | name:<norm>|<state>
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    cin: Mapped[str | None] = mapped_column(String(30), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    state: Mapped[str | None] = mapped_column(String(80))
    sources: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    registry_ids: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    triage_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    triage_reasons: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    # new -> triaged_out | queued -> light_done (fit | not_fit | unknown) -> promoted
    stage: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(12))
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    verdict_evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list, nullable=False)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_hash: Mapped[str | None] = mapped_column(String(64))           # content_hash at last light check
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Task(Base):
    """DB-backed work queue (light | deep | verify). Workers claim with row locks; results commit immediately."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("kind", "subject_id", name="uq_task_kind_subject"),
        Index("ix_tasks_claim", "status", "kind", "priority"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)     # prospect id (light) / company id (deep, verify)
    status: Mapped[str] = mapped_column(String(10), default="queued", nullable=False)  # queued | running | done | failed
    priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class LoadCheckpoint(Base):
    """Resumable bulk loads (e.g. data.gov.in paging per resource)."""

    __tablename__ = "load_checkpoints"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    resource: Mapped[str] = mapped_column(String(200), primary_key=True)
    next_offset: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int | None] = mapped_column(Integer)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ProviderQuota(Base):
    """Usage counter per provider per period ('2026-10' for monthly, '2026-10-25' for daily)."""

    __tablename__ = "provider_quota"

    provider: Mapped[str] = mapped_column(String(80), primary_key=True)
    period: Mapped[str] = mapped_column(String(10), primary_key=True)
    used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    limit: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PolicyVersion(Base):
    """AI Policy versions (policy.py). Exactly one is `active`; every score uses it (YAML when none is saved)."""

    __tablename__ = "policy_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    version: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="draft", nullable=False)   # active | draft | archived
    author: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
