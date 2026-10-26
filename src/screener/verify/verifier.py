"""Verifier agent: a separate Gemini-fast call that sees ONLY claim statements and the freshly fetched page
text. It never sees the extractor's output (no evidence spans, no extraction context)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SYSTEM = (
    "You are an independent fact checker. You are given numbered CLAIMS and the text of ONE web page. "
    "For each claim decide: 'supported' only if the page explicitly states it; 'contradicted' if the page "
    "states something incompatible with it; 'not_found' if the page does not address it. Use only the page "
    "text, never outside knowledge. Confidence is your certainty in that judgement (0 to 1). One short reason."
)


class Verdict(BaseModel):
    claim_id: int
    status: Literal["supported", "contradicted", "not_found"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


def _money(v: dict[str, Any]) -> str:
    if v.get("amount_original") is None:
        return ""
    unit = f" {v['unit_original']}" if v.get("unit_original") else ""
    cur = v.get("currency_original") or ""
    return f"{cur} {v['amount_original']:g}{unit}".strip()


def statement(field: str, value: dict[str, Any], subject: str) -> str:
    """Plain-language claim the verifier checks."""
    v = value
    match field:
        case "description":
            return f"{subject} is described as: {v.get('text')}"
        case "city":
            return f"{subject} is based in {v.get('city')}."
        case "country":
            return f"{subject} is based in {v.get('country')}."
        case "founded_year":
            return f"{subject} was founded in {v.get('year')}."
        case "headcount":
            return f"{subject}'s team size is {v.get('text')}."
        case "legal_name":
            return f"The legal entity name of {subject} is {v.get('name')}."
        case "incorporation_date":
            return f"{subject} was incorporated on {v.get('date')}."
        case "director":
            return f"{v.get('name')} is a director of {subject}."
        case "segment" | "capability":
            return f"{subject} works in: {v.get('label')}."
        case "open_roles":
            return f"{subject} lists {v.get('count')} open job roles."
        case "funding_round":
            parts = [p for p in (v.get("stage"), _money(v)) if p]
            inv = f" from {', '.join(v['investors'])}" if v.get("investors") else ""
            when = f" ({v['date']})" if v.get("date") else ""
            return f"{subject} raised {' '.join(parts) or 'a funding round'}{inv}{when}."
        case "acquired":
            return f"{subject} was acquired by {v.get('acquirer')}" + (f" ({v['date']})." if v.get("date") else ".")
        case "revenue":
            return f"{subject}'s revenue in {v.get('fiscal_year')} was {_money(v)}."
        case "total_funding":
            return f"{subject} has raised {_money(v) or 'funding'} in total" + (f" (as of {v['as_of']})." if v.get("as_of") else ".")
        case "latest_news_date":
            return f"The article titled '{v.get('title')}' was published on {v.get('date')}."
        case "github_stars":
            return f"The GitHub repository {v.get('repo')} has {v.get('stars')} stars."
        case "github_last_push":
            return f"The GitHub repository {v.get('repo')} was last pushed on {v.get('date')}."
        case "current_role":
            return f"{subject} is {v.get('role')} of {v.get('company')}."
        case "prior_role":
            years = "-".join(str(y) for y in (v.get("start_year"), v.get("end_year")) if y)
            return f"{subject} previously worked at {v.get('company')}" + (f" as {v['role']}" if v.get("role") else "") + (f" ({years})." if years else ".")
        case "prior_exit":
            return f"{v.get('company')}, a company {subject} was part of, was acquired" + (f" by {v['acquirer']}." if v.get("acquirer") else ".")
        case "education":
            return f"{subject} studied at {v.get('institution')}" + (f" ({v['degree']})." if v.get("degree") else ".")
    return f"{subject}: {field} = {value}"


async def verify_batch(gemini: Any, statements: list[str], page_text: str) -> list[Verdict]:
    claims = "\n".join(f"[{i}] {s}" for i, s in enumerate(statements))
    prompt = f"CLAIMS:\n{claims}\n\nPAGE TEXT:\n{page_text}"
    result = await gemini.generate_json(prompt, Verdicts, "fast", system=SYSTEM)
    return result.verdicts
