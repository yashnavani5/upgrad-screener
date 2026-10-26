"""Row contracts for the v_* database views: the API between backend and frontend.

Field names and types match docs/ARCHITECTURE.md section 4 exactly. Changing a model here is a contract
change: update the view migration, run `screener export-schema` and `npm run gen:types`, and commit all
of them together (see contracts/README.md).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Fit categories are defined by the AI Policy (default: acquire, partner, co_build); ids are lowercase snake_case.
FitType = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,19}$")]
RunStatus = Literal["queued", "running", "succeeded", "failed", "partial"]
Stage = Literal["discover", "resolve", "enrich", "verify", "score", "explain", "publish"]
FunnelStage = Literal["discovered", "resolved", "enriched", "verified", "gated", "scored", "shortlisted"]
RunTrigger = Literal["cli", "dashboard", "schedule"]
CompanyStatus = Literal["active", "inactive", "unknown"]
GateStatus = Literal["passed", "failed"]
Region = Literal["india", "outside", "unknown"]  # verified location (score/features.py infer_geo)
ReasonCode = Literal[
    "TOO_LARGE",
    "COMPETITOR",
    "UPGRAD_OWNED",
    "UNVERIFIABLE",
    "INACTIVE",
    "LOW_FIT",
    "NOT_EDUCATION",
    "DUPLICATE_THESIS",
    "ACQUIRED",
    "POLICY_FILTER",
]
ScoreName = Literal[
    "T", "M", "N", "I", "F_acquire", "F_partner", "F_co_build", "F", "C", "final", "bt_pct", "final_prime"
]

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
"""A score, confidence or progress value in [0, 1]."""


class ContractModel(BaseModel):
    """Base for view rows: immutable, and unknown columns are an error (catches view/contract drift)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# Nested JSON shapes


class FounderRef(ContractModel):
    name: str
    linkedin_url: str | None = None
    role: str | None = None


class FounderFact(ContractModel):
    """A supported founder claim shown as background: prior_role, prior_exit or education."""
    field: Literal["prior_role", "prior_exit", "education"]
    value: dict[str, Any]
    source_url: str


class FounderProfile(FounderRef):
    """FounderRef plus the verified profile (v_shortlist)."""
    founder_id: UUID | None = None
    title: str | None = None
    identity_verified: bool = False
    x_url: str | None = None          # X profile found via search snippets (never fetched); never verifies alone
    background: list[FounderFact] = []
    line: str | None = None           # one-line bio written from the founder's verified claims (explain stage)


class FounderDetail(ContractModel):
    founder_id: UUID
    name: str
    linkedin_url: str | None = None
    role: str | None = None
    is_current: bool
    title: str | None = None
    identity_verified: bool = False
    x_url: str | None = None
    background: list[FounderFact] = []
    line: str | None = None


class EvidenceRef(ContractModel):
    url: str
    title: str | None = None
    domain: str


class RoundRef(ContractModel):
    round_type: str | None = None
    amount_usd: float | None = None
    announced_on: date | None = None
    investors: list[str] = []
    source_url: str


class ScoreComponent(ContractModel):
    name: ScoreName
    raw: float | None = None
    adjusted: float | None = None
    n_signals: int = Field(ge=0)
    pool_mean: float | None = None
    weight: float | None = None
    why_not_higher: str | None = None


class ClaimRef(ContractModel):
    field: str
    value: Any
    source_url: str
    evidence_span: str
    retrieved_at: datetime
    verifier_confidence: Unit | None = None


class RunCounts(ContractModel):
    candidates: int = 0
    companies: int = 0
    founders: int = 0
    claims_supported: int = 0
    shortlisted: int = 0


class ProviderUsage(ContractModel):
    calls: int = 0
    errors: int = 0
    cost_usd: float = 0.0
    quota_used: float | None = None
    quota_limit: float | None = None


class ConfigSnapshot(ContractModel):
    weights: dict[str, Any]
    gap_map: dict[str, Any]
    exclusions: dict[str, Any]
    models: dict[str, str]


# View rows


class ShortlistRow(ContractModel):
    """v_shortlist: one row per shortlisted entity per run."""

    run_id: UUID
    is_latest: bool
    rank: int = Field(ge=1)
    entity_id: UUID
    company: str
    website: str | None = None
    founders: list[FounderProfile]
    fit_type: FitType
    final: Unit
    t: Unit
    i: Unit
    f: Unit
    confidence: Unit
    why_line: str
    fit_line: str
    evidence: list[EvidenceRef]
    last_verified: datetime | None = None
    reviewer_label: str | None = None
    region: Region = "unknown"
    region_basis: str = "no location evidence"
    size_verified: bool = False  # verified funding or headcount behind the size check
    description: str | None = None
    reviewer_note: str | None = None  # the reviewer's view (Sheet or reviewer_picks.yaml), never a verified fact
    reviewer: str | None = None       # who wrote it: a reviewer name or the picks file's origin


class CompanyDetailRow(ContractModel):
    """v_company_detail: one row per entity per run."""

    run_id: UUID
    is_latest: bool
    entity_id: UUID
    company: str
    website: str | None = None
    description: str | None = None
    country: str | None = None
    city: str | None = None
    founded_year: int | None = None
    status: CompanyStatus
    total_raised_usd: float | None = None
    headcount: int | None = None
    fit_type: FitType | None = None
    rank: int | None = None
    founders: list[FounderDetail]
    rounds: list[RoundRef]
    score_breakdown: list[ScoreComponent]
    claims: list[ClaimRef]
    region: Region = "unknown"
    region_basis: str = "no location evidence"


class LeftOutRow(ContractModel):
    """v_left_out: one row per excluded entity per run."""

    run_id: UUID
    is_latest: bool
    entity_id: UUID
    company: str
    founders: list[FounderRef]
    reason_code: ReasonCode
    explanation: str
    would_have_ranked: int | None = None
    source_url: str | None = None
    region: Region = "unknown"
    featured: int | None = None              # 1..3: curated "left out" pick, in display order
    featured_category: Literal["big", "evidence", "close"] | None = None
    featured_line: str | None = None         # cited line on why it was left out
    description: str | None = None


class LongListRow(ContractModel):
    """v_long_list: top 200 per run by Final'."""

    run_id: UUID
    is_latest: bool
    rank: int = Field(ge=1)
    entity_id: UUID
    company: str
    website: str | None = None
    founders: list[FounderRef]
    fit_type: FitType | None = None
    final: Unit
    t: Unit | None = None
    m: Unit | None = None
    n: Unit | None = None
    i: Unit | None = None
    f: Unit | None = None
    f_acquire: Unit | None = None
    f_partner: Unit | None = None
    f_co_build: Unit | None = None
    confidence: Unit
    bt_pct: Unit | None = None
    gate_status: GateStatus
    reason_code: ReasonCode | None = None
    region: Region = "unknown"


class RunRow(ContractModel):
    """v_runs: one row per pipeline run."""

    run_id: UUID
    status: RunStatus
    current_stage: Stage | None = None
    stage_progress: Unit
    trigger: RunTrigger
    started_at: datetime
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    counts: RunCounts
    provider_usage: dict[str, ProviderUsage]
    cost_usd: float = Field(ge=0.0)
    error_summary: str | None = None
    config_snapshot: ConfigSnapshot | None = None
    sheet_url: str | None = None
    is_latest: bool
    checkpoints: dict[str, Any] = {}   # {stage: {status, summary, started_at, finished_at, retry}}
    options: dict[str, Any] = {}       # options the run was started with (top, incremental, ...)


class FunnelRow(ContractModel):
    """v_funnel: one row per funnel stage per run."""

    run_id: UUID
    is_latest: bool
    stage: FunnelStage
    stage_order: int = Field(ge=1, le=7)
    count: int = Field(ge=0)


class ActivityRow(ContractModel):
    """v_activity: external calls per provider (from api_calls), for the live dashboard."""

    provider: str
    calls_5m: int = Field(ge=0)
    errors_5m: int = Field(ge=0)
    calls_1h: int = Field(ge=0)
    median_latency_ms_1h: int | None = None
    last_call_at: datetime | None = None


class UniverseRow(ContractModel):
    """v_universe: counts across registries, prospects, queue, claims, companies, founders."""

    area: str
    bucket: str
    count: int = Field(ge=0)


class SearchRow(ContractModel):
    """v_search: one searchable row per company, founder and registry entry (MCA / DPIIT)."""

    kind: Literal["company", "founder", "registry"]
    entity_id: UUID
    company_id: UUID | None = None          # the company page to open (companies and founders)
    name: str
    company: str | None = None              # founders: their company
    website: str | None = None
    description: str | None = None          # founders: title; registry: industry classification
    location: str | None = None
    region: Region = "unknown"
    shortlist_rank: int | None = None       # latest published run
    rank: int | None = None
    fit_type: FitType | None = None
    final: float | None = None
    status: str | None = None
    cin: str | None = None                  # registry rows: MCA corporate identification number
    registered_on: date | None = None
    paid_up_capital_inr: float | None = None
    authorized_capital_inr: float | None = None
    total_raised_usd: float | None = None


class PolicyRow(ContractModel):
    """v_policy: AI Policy versions (one is active). `policy` follows screener.policy.Policy."""

    version: int
    status: Literal["active", "draft", "archived"]
    author: str
    note: str
    policy: dict[str, Any]
    changes: list[dict[str, Any]]
    created_at: datetime
    runs_scored: int = 0


# View name -> row model. Drives `screener export-schema` and the view/contract column-parity test.
VIEW_CONTRACTS: dict[str, type[ContractModel]] = {
    "v_shortlist": ShortlistRow,
    "v_company_detail": CompanyDetailRow,
    "v_left_out": LeftOutRow,
    "v_long_list": LongListRow,
    "v_runs": RunRow,
    "v_funnel": FunnelRow,
    "v_activity": ActivityRow,
    "v_universe": UniverseRow,
    "v_search": SearchRow,
    "v_policy": PolicyRow,
}
