"""Gemini structured-output schemas and prompts for enrichment. The LLM extracts; code decides everything else."""

from __future__ import annotations

from pydantic import BaseModel, Field

REQUIRED = "Return null if the text does not state it. Copy evidence_span verbatim."

BASE_RULES = (
    "You extract facts about ONE target company (and its founders) from a single web page for a due-diligence "
    f"pipeline. {REQUIRED} Every evidence_span must be an exact, contiguous quote from the page text that states "
    "the fact. Never infer, estimate, translate or combine facts from different sentences. If the page is about a "
    "different company, return nothing for it."
)

SPAN = Field(description="Exact quote from the page that states this fact.")


class TextFact(BaseModel):
    value: str | None = None
    evidence_span: str | None = SPAN


class IntFact(BaseModel):
    value: int | None = None
    evidence_span: str | None = SPAN


class TagFact(BaseModel):
    tag: str = Field(description="One id from the allowed vocabulary.")
    evidence_span: str = SPAN


class PersonRole(BaseModel):
    name: str
    role: str | None = None
    evidence_span: str = SPAN


class SiteFacts(BaseModel):
    description: TextFact | None = Field(None, description="One-sentence description of what the company does, as stated.")
    city: TextFact | None = None
    country: TextFact | None = None
    founded_year: IntFact | None = None
    headcount: TextFact | None = Field(None, description="Team size exactly as stated, e.g. '50+ employees'.")
    legal_name: TextFact | None = Field(None, description="Registered legal entity name, e.g. from the footer.")
    segments: list[TagFact] = Field(default_factory=list, description="upGrad business units this company operates in.")
    capabilities: list[TagFact] = Field(default_factory=list, description="Capabilities from the wanted-capability list.")
    people: list[PersonRole] = Field(default_factory=list, description="Founders and leaders named on the page.")
    open_roles: IntFact | None = Field(None, description="Number of open job positions if the page lists or states it.")


class RoundFact(BaseModel):
    date: str | None = Field(None, description="Date as stated (YYYY, YYYY-MM or YYYY-MM-DD).")
    stage: str | None = Field(None, description="Seed, Pre-Series A, Series A, ... as stated.")
    amount: float | None = Field(None, description="Number as written, without unit (e.g. 5 for '$5 million').")
    unit: str | None = Field(None, description="million, billion, crore, lakh, thousand or null for plain numbers.")
    currency: str | None = Field(None, description="USD or INR as stated.")
    investors: list[str] = Field(default_factory=list)
    evidence_span: str = SPAN


class AcquisitionFact(BaseModel):
    acquirer: str
    date: str | None = None
    evidence_span: str = SPAN


class NewsFacts(BaseModel):
    about_target: bool = Field(description="True only if the article is about the target company.")
    rounds: list[RoundFact] = Field(default_factory=list, description="Funding rounds raised BY the target company.")
    acquired_by: AcquisitionFact | None = Field(None, description="Only if the target company itself was acquired.")
    people: list[PersonRole] = Field(default_factory=list, description="Founders of the target named in the article.")


class TotalFundingFact(BaseModel):
    amount: float = Field(description="Number as written, without unit (e.g. 120 for '$120 million').")
    unit: str | None = Field(None, description="million, billion, crore, lakh, thousand or null.")
    currency: str | None = Field(None, description="USD or INR as stated.")
    as_of: str | None = Field(None, description="Date or year the total refers to, if stated.")
    evidence_span: str = SPAN


class SizeFacts(BaseModel):
    about_target: bool = Field(description="True only if the page is about the target company.")
    total_funding: TotalFundingFact | None = Field(
        None, description="Total funding raised BY the target to date, only if the page states a total.")
    headcount: TextFact | None = Field(None, description="Employee count or team size of the target, as stated.")
    rounds: list[RoundFact] = Field(default_factory=list, description="Funding rounds raised BY the target company.")


class LocationFacts(BaseModel):
    about_target: bool = Field(description="True only if the page is about the target company.")
    city: TextFact | None = Field(None, description="City where the target company is headquartered or based.")
    country: TextFact | None = Field(None, description="Country where the target company is headquartered or based.")


class RevenueFact(BaseModel):
    fiscal_year: str = Field(description="e.g. FY24 or FY2023-24, as stated.")
    amount: float
    unit: str | None = None
    currency: str | None = None
    evidence_span: str = SPAN


class RevenueFacts(BaseModel):
    revenues: list[RevenueFact] = Field(default_factory=list, description="Operating revenue of the target company only.")


class RegistryFacts(BaseModel):
    legal_name: TextFact | None = None
    incorporation_date: TextFact | None = None
    directors: list[PersonRole] = Field(default_factory=list)


class CareerFact(BaseModel):
    company: str
    role: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    exit: bool = Field(False, description="True only if the text says that company was acquired or went public.")
    acquirer: str | None = None
    evidence_span: str = SPAN


class EducationFact(BaseModel):
    institution: str
    degree: str | None = None
    year: int | None = None
    evidence_span: str = SPAN


class FounderProfile(BaseModel):
    name: str
    current_role: str | None = Field(None, description="Role at the target company, if stated.")
    current_role_span: str | None = SPAN
    careers: list[CareerFact] = Field(default_factory=list, description="PRIOR companies/roles (not the target).")
    education: list[EducationFact] = Field(default_factory=list)


class FounderFacts(BaseModel):
    founders: list[FounderProfile] = Field(default_factory=list)


class SnippetPerson(BaseModel):
    result_id: int
    name: str
    role: str | None = None
    evidence_span: str = SPAN


class SnippetPeople(BaseModel):
    people: list[SnippetPerson] = Field(default_factory=list)


def target_header(name: str, aliases: list[str], domain: str | None, founders: list[str]) -> str:
    return (
        f"TARGET COMPANY: {name}" + (f" (also known as: {', '.join(aliases[:5])})" if aliases else "")
        + (f"; website domain: {domain}" if domain else "")
        + (f"; known founders: {', '.join(founders[:6])}" if founders else "")
    )
