"""Explanations for shortlisted companies, from verified claims only.

Gemini-smart writes "why they made the cut" and "how they fit upGrad" as segments, each citing claim ids.
Code validates every segment (real claim ids, numbers present in the cited claims, <= 30 words per line,
fit line names the fit type and a business unit/capability) and asks again with the errors (max 3 attempts).
If the model still fails, the lines are assembled from the claims in code, so an unsupported sentence is
never published."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from screener.resolve.normalize import registered_domain
from screener.scouts.grounding import _numbers
from screener.verify.rules import is_primary
from screener.verify.verifier import statement

log = logging.getLogger(__name__)

MAX_WORDS = 30
MAX_ATTEMPTS = 3
FIT_NAMES = {"acquire": "Acquire", "partner": "Partner", "co_build": "Co-build"}

SYSTEM = (
    "You write two short lines about an education company for upGrad's partnerships / M&A / co-build team, "
    "using ONLY the numbered verified claims. Each line is a list of segments; every segment must cite the "
    "claim ids (like C3) that support it. No facts, numbers, names or adjectives that the cited claims do not "
    "state. Line 1 (why): why the company made the shortlist, <= 30 words. Line 2 (fit): how it fits upGrad, "
    "<= 30 words, naming the fit type and the specific upGrad business unit or wanted capability."
)


class Segment(BaseModel):
    text: str
    claim_ids: list[str] = Field(description="Claim ids (e.g. C3) that support this segment.")


class Explanation(BaseModel):
    why: list[Segment]
    fit: list[Segment]


@dataclass
class ClaimRef:
    label: str            # C1, C2, ...
    claim_id: str
    statement: str
    field: str
    value: dict[str, Any]
    source_url: str
    confidence: float | None
    source_type: str


@dataclass
class Written:
    why_line: str
    fit_line: str
    why_ids: list[str]
    fit_ids: list[str]
    method: str                       # llm | template
    attempts: int
    errors: list[str] = field(default_factory=list)


def words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def fit_name(fit_type: str, names: dict[str, str] | None = None) -> str:
    """Display name of a fit category: the AI Policy label, else the default, else the id in words."""
    return (names or {}).get(fit_type) or FIT_NAMES.get(fit_type) or fit_type.replace("_", " ").capitalize()


def validate(exp: Explanation, refs: dict[str, ClaimRef], fit_type: str, labels: list[str],
             names: dict[str, str] | None = None) -> list[str]:
    """Every segment must cite existing claims and every number must appear in a cited claim."""
    errors: list[str] = []
    for line_name, segs in (("why", exp.why), ("fit", exp.fit)):
        if not segs:
            errors.append(f"{line_name}: empty")
            continue
        for seg in segs:
            ids = [i.strip().upper() for i in seg.claim_ids]
            missing = [i for i in ids if i not in refs]
            if not ids or missing:
                errors.append(f"{line_name}: segment '{seg.text}' has no valid claim id {missing or ''}".strip())
                continue
            cited = " ".join(refs[i].statement for i in ids)
            extra = _numbers(seg.text) - _numbers(cited)
            if extra:
                errors.append(f"{line_name}: segment '{seg.text}' states numbers not in its claims: {sorted(extra)}")
        line = join(segs)
        if words(line) > MAX_WORDS:
            errors.append(f"{line_name}: {words(line)} words (max {MAX_WORDS})")
    fit_line = join(exp.fit).lower()
    if fit_name(fit_type, names).lower() not in fit_line and fit_type.replace("_", " ") not in fit_line:
        errors.append(f"fit: must name the fit type '{fit_name(fit_type, names)}'")
    if labels and not any(l.lower() in fit_line for l in labels):
        errors.append(f"fit: must name one of these upGrad units/capabilities: {labels[:6]}")
    return errors


DASHES = re.compile(r"\s*[\u2014\u2013]\s*")


def plain(text: str) -> str:
    """House style: no em or en dashes in generated copy; a dash between clauses becomes a comma."""
    return DASHES.sub(", ", text)


def join(segs: list[Segment]) -> str:
    text = plain(re.sub(r"\s+", " ", " ".join(s.text.strip().rstrip(".") for s in segs)).strip())
    return text if text.endswith(".") else text + "."


def build_refs(claims: list[Any], subjects: dict[Any, str]) -> dict[str, ClaimRef]:
    refs = {}
    for i, c in enumerate(claims, start=1):
        label = f"C{i}"
        refs[label] = ClaimRef(label, str(c.id), statement(c.field, c.value_json or {}, subjects.get(c.entity_id, "The company")),
                               c.field, c.value_json or {}, c.source_url, c.verifier_confidence, c.source_type)
    return refs


def fit_labels(company_claims: list[Any], gap_map: dict[str, Any]) -> list[str]:
    """Business units / capabilities the company is verified to have; else the whole gap map."""
    tagged = [c.value_json.get("label") for c in company_claims if c.field in ("segment", "capability") and c.value_json.get("label")]
    if tagged:
        return sorted(set(tagged))
    return [b["name"] for b in gap_map.get("business_units", [])] + [w["name"] for w in gap_map.get("wanted_capabilities", [])]


def template(refs: dict[str, ClaimRef], fit_type: str, labels: list[str], names: dict[str, str] | None = None) -> Written:
    """Code-built lines from the strongest claims (used when the model cannot produce a valid answer)."""
    order = ("funding_round", "revenue", "acquired", "prior_exit", "prior_role", "current_role", "description",
             "open_roles", "github_stars", "education")
    ranked = sorted(refs.values(), key=lambda r: (order.index(r.field) if r.field in order else 99, -(r.confidence or 0)))
    why_refs, why_words = [], 0
    for r in ranked:
        n = words(r.statement)
        if why_words + n > MAX_WORDS:
            continue
        why_refs.append(r)
        why_words += n
        if len(why_refs) == 2:
            break
    why = " ".join(r.statement.rstrip(".") + "." for r in why_refs) or "Verified claims available in the dossier."
    tag = next((r for r in refs.values() if r.field in ("capability", "segment")), None)
    label = (tag.value.get("label") if tag else None) or (labels[0] if labels else "")
    fit = f"{fit_name(fit_type, names)} fit: {label}."
    fit_ids = [tag.label] if tag else []
    return Written(why, fit, [r.claim_id for r in why_refs], [refs[i].claim_id for i in fit_ids], "template", 0)


async def write(gemini: Any, company_name: str, fit_type: str, claims: list[Any], subjects: dict[Any, str],
                gap_map: dict[str, Any], names: dict[str, str] | None = None) -> Written:
    refs = build_refs(claims, subjects)
    company_claims = [c for c in claims if c.entity_type == "company"]
    labels = fit_labels(company_claims, gap_map)
    if not refs or gemini is None:
        return template(refs, fit_type, labels, names)
    base = (f"COMPANY: {company_name}\nFIT TYPE: {fit_name(fit_type, names)}\nupGrad units/capabilities to name in the fit "
            f"line (pick one): {', '.join(labels[:12])}\n\nVERIFIED CLAIMS:\n"
            + "\n".join(f"{r.label}: {r.statement}" for r in refs.values()))
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = base + (f"\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n- " + "\n- ".join(errors) + "\nFix every issue." if errors else "")
        try:
            exp = await gemini.generate_json(prompt, Explanation, "smart", system=SYSTEM)
        except Exception as exc:
            errors = [f"generation failed: {type(exc).__name__}"]
            continue
        errors = validate(exp, refs, fit_type, labels, names)
        if not errors:
            ids = lambda segs: sorted({refs[i.strip().upper()].claim_id for s in segs for i in s.claim_ids})
            return Written(join(exp.why), join(exp.fit), ids(exp.why), ids(exp.fit), "llm", attempt)
    log.info("writer fell back to template for %s: %s", company_name, errors)
    w = template(refs, fit_type, labels, names)
    w.attempts, w.errors = MAX_ATTEMPTS, errors
    return w


# --- evidence ----------------------------------------------------------------------------------------

WEAK_EVIDENCE_DOMAINS = {"linkedin.com", "x.com", "twitter.com", "bebee.com", "indeed.com", "naukri.com", "glassdoor.com",
                         "glassdoor.co.in", "facebook.com", "instagram.com", "youtube.com"}
EVIDENCE_FIELDS = ("funding_round", "revenue", "acquired", "prior_exit", "description", "current_role", "segment",
                   "capability", "legal_name", "incorporation_date", "latest_news_date", "open_roles", "github_stars")


def pick_evidence(claims: list[Any], used_ids: set[str], company_domain: str | None, vconf: dict[str, Any],
                  k: int = 3) -> list[dict[str, Any]]:
    """3 strongest evidence URLs: primary sources first, then claims the writer used, then domain diversity."""
    def rank(c: Any) -> tuple[int, int, int, float]:
        primary = is_primary(c.source_url, company_domain, [], vconf)
        return (0 if primary else 1, 0 if str(c.id) in used_ids else 1,
                EVIDENCE_FIELDS.index(c.field) if c.field in EVIDENCE_FIELDS else 99, -(c.verifier_confidence or 0))

    out: list[dict[str, Any]] = []
    domains: set[str] = set()
    def weak(c: Any) -> bool:  # social profiles and job boards: last resort as shown evidence
        d = registered_domain(c.source_url) or ""
        return (c.meta or {}).get("text_mode") == "snippet" or d in WEAK_EVIDENCE_DOMAINS

    ordered = sorted((c for c in claims if not weak(c)), key=rank) + sorted((c for c in claims if weak(c)), key=rank)
    for diverse in (True, False):
        for c in ordered:
            d = registered_domain(c.source_url) or ""
            if any(e["url"] == c.source_url for e in out) or (diverse and d in domains):
                continue
            out.append({"url": c.source_url, "title": (c.value_json or {}).get("title"), "domain": d,
                        "claim_id": str(c.id), "primary": is_primary(c.source_url, company_domain, [], vconf)})
            domains.add(d)
            if len(out) == k:
                return out
    return out
