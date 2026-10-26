"""Founder one-liners and the curated "left out" picks, from verified claims only.

- founder lines, one model call per founder, every segment citing claim ids (code validates ids and numbers; a
  code-built fallback is used if the model fails):
    line (<= 25 words): who the founder is (role, prior companies, exits, education)
    why  (<= 25 words): why the founder made the cut: their verified track record
    fit  (<= 25 words): how their background fits upGrad, naming one upGrad business unit or wanted capability
- curated left out: by rule, up to 3 exclusions that show judgement, one per category, India-located first:
    big:       TOO_LARGE / COMPETITOR                  (already a scaled player; upGrad's own brands are never shown)
    evidence:  UNVERIFIABLE / INACTIVE                 (could not be verified enough to recommend)
    close:     DUPLICATE_THESIS / LOW_FIT              (good, but beaten by a similar or stronger pick)
  each with a <= 40-word line citing the exclusion (a code fact) and the company's verified claims.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from screener.explain.writer import ClaimRef, Segment, build_refs, join, words
from screener.scouts.grounding import _numbers

log = logging.getLogger(__name__)

FOUNDER_FIELDS = ("current_role", "prior_role", "prior_exit", "education")
FOUNDER_MAX_WORDS = 25
CURATED_MAX_WORDS = 40
MAX_ATTEMPTS = 3
CATEGORIES = (
    ("big", ("TOO_LARGE", "COMPETITOR", "ACQUIRED"), "already a scaled player or acquired"),
    ("evidence", ("UNVERIFIABLE", "INACTIVE"), "not enough verified evidence"),
    ("close", ("DUPLICATE_THESIS", "LOW_FIT"), "a close call"),
)

FOUNDER_SYSTEM = (
    "Write THREE short lines about a founder for upGrad's partnerships / M&A / co-build team, using ONLY the "
    "numbered verified claims (current role, prior companies and roles, exits, education). Each line is a list of "
    "segments and every segment cites claim ids (like C2). No adjectives, numbers or facts the cited claims do not "
    "state. No em dashes. "
    "line (<= 25 words): who the founder is. "
    "why (<= 25 words): why this founder made the cut: their verified track record (exits, companies built or "
    "scaled, senior roles, education). "
    "fit (<= 25 words): how this founder's background fits upGrad, naming exactly one of the given upGrad business "
    "units or wanted capabilities."
)
CURATED_SYSTEM = (
    "Write ONE line (<= 40 words) explaining why this education company was LEFT OUT of upGrad's shortlist. Start "
    "from the REASON (a fact computed by the screener, cite it as R) and add what the company does from the "
    "numbered verified claims. Each segment cites ids (R or C1, C2...). No facts, numbers or adjectives that the "
    "cited items do not state. No em dashes."
)


class Line(BaseModel):
    segments: list[Segment]


class FounderLines(BaseModel):
    line: list[Segment]
    why: list[Segment]
    fit: list[Segment]


@dataclass
class FounderWritten:
    line: str
    why: str
    fit: str
    claim_ids: list[str]
    method: str        # llm | template
    attempts: int


@dataclass
class Written:
    line: str
    claim_ids: list[str]
    method: str        # llm | template
    attempts: int


def check(line: Line, refs: dict[str, ClaimRef], max_words: int, extra: dict[str, str] | None = None) -> list[str]:
    """Every segment cites known ids; every number appears in what it cites; length limit."""
    extra = extra or {}
    errors = []
    if not line.segments:
        return ["empty line"]
    for seg in line.segments:
        ids = [i.strip().upper() for i in seg.claim_ids]
        bad = [i for i in ids if i not in refs and i not in extra]
        if not ids or bad:
            errors.append(f"segment '{seg.text}' has no valid id {bad or ''}".strip())
            continue
        cited = " ".join(refs[i].statement if i in refs else extra[i] for i in ids)
        if missing := _numbers(seg.text) - _numbers(cited):
            errors.append(f"segment '{seg.text}' states numbers not in its sources: {sorted(missing)}")
    if words(join(line.segments)) > max_words:
        errors.append(f"{words(join(line.segments))} words (max {max_words})")
    return errors


async def _write(gemini: Any, system: str, header: str, refs: dict[str, ClaimRef], max_words: int,
                 extra: dict[str, str] | None = None) -> tuple[Line | None, int, list[str]]:
    base = header + "\n\nVERIFIED CLAIMS:\n" + "\n".join(f"{r.label}: {r.statement}" for r in refs.values())
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = base + ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n- " + "\n- ".join(errors) + "\nFix every issue." if errors else "")
        try:
            line = await gemini.generate_json(prompt, Line, "smart", system=system)
        except Exception as exc:
            errors = [f"generation failed: {type(exc).__name__}"]
            continue
        errors = check(line, refs, max_words, extra)
        if not errors:
            return line, attempt, []
    return None, MAX_ATTEMPTS, errors


def _founder_parts(refs: dict[str, ClaimRef]) -> dict[str, list[tuple[str, str]]]:
    """(text, claim_id) per founder field, in the order a reader wants them."""
    by = {f: [r for r in refs.values() if r.field == f] for f in FOUNDER_FIELDS}
    text = {"current_role": lambda v: f"{v.get('role')} of {v.get('company')}",
            "prior_exit": lambda v: f"part of {v.get('company')}, acquired" + (f" by {v['acquirer']}" if v.get("acquirer") else ""),
            "prior_role": lambda v: f"previously {v.get('role') + ' at ' if v.get('role') else ''}{v.get('company')}",
            "education": lambda v: f"studied at {v.get('institution')}"}
    return {f: [(text[f](r.value), r.claim_id) for r in rs] for f, rs in by.items()}


def _fit_template(name: str, parts: dict[str, list[tuple[str, str]]], labels: list[str]) -> tuple[str, list[str]]:
    label = labels[0] if labels else "programmes"
    for field_ in ("prior_role", "current_role"):
        if parts[field_]:
            text, cid = parts[field_][0]
            return f"{name}: {text}, relevant to upGrad's {label}.", [cid]
    return f"{name}: no verified background yet to map to upGrad's {label}.", []


def founder_template(name: str, refs: dict[str, ClaimRef], labels: list[str] | None = None) -> FounderWritten:
    """Code-built lines from verified claims only: intro (role, exits, prior roles, education), why (track record
    without the current role), fit (a prior or current role next to one upGrad label)."""
    parts = _founder_parts(refs)

    def build(order: tuple[tuple[str, int], ...]) -> tuple[list[str], list[str]]:
        texts, ids = [], []
        for field_, n in order:
            for text, cid in parts[field_][:n]:
                if words(" ".join(texts + [text])) > FOUNDER_MAX_WORDS - 2:
                    break
                texts.append(text)
                ids.append(cid)
        return texts, ids

    intro, intro_ids = build((("current_role", 1), ("prior_exit", 1), ("prior_role", 2), ("education", 1)))
    track, track_ids = build((("prior_exit", 1), ("prior_role", 2), ("education", 1)))
    fit, fit_ids = _fit_template(name, parts, labels or [])
    line = (f"{name}: " + "; ".join(intro) + ".") if intro else f"{name}: no verified background beyond the founding role yet."
    why = (f"{name}: " + "; ".join(track) + ".") if track else f"{name}: no verified track record beyond the founding role yet."
    return FounderWritten(re.sub(r"\s+", " ", line), re.sub(r"\s+", " ", why), re.sub(r"\s+", " ", fit),
                          sorted(set(intro_ids + track_ids + fit_ids)), "template", 0)


def check_founder(out: FounderLines, refs: dict[str, ClaimRef], labels: list[str]) -> list[str]:
    errors = []
    for part in ("line", "why", "fit"):
        errors += [f"{part}: {e}" for e in check(Line(segments=getattr(out, part)), refs, FOUNDER_MAX_WORDS)]
    fit = join(out.fit).lower()
    if labels and not any(label.lower() in fit for label in labels):
        errors.append(f"fit: must name one of: {', '.join(labels[:12])}")
    return errors


async def founder_lines(gemini: Any, name: str, company: str, claims: list[Any], labels: list[str]) -> FounderWritten:
    own = [c for c in claims if c.field in FOUNDER_FIELDS]
    refs = build_refs(own, {c.entity_id: name for c in own})
    if not refs or gemini is None:
        return founder_template(name, refs, labels)
    base = (f"FOUNDER: {name}\nCOMPANY: {company}\nupGrad units/capabilities to name in the fit line (pick one): "
            f"{', '.join(labels[:12])}\n\nVERIFIED CLAIMS:\n" + "\n".join(f"{r.label}: {r.statement}" for r in refs.values()))
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = base + ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n- " + "\n- ".join(errors) + "\nFix every issue." if errors else "")
        try:
            out = await gemini.generate_json(prompt, FounderLines, "smart", system=FOUNDER_SYSTEM)
        except Exception as exc:
            errors = [f"generation failed: {type(exc).__name__}"]
            continue
        errors = check_founder(out, refs, labels)
        if not errors:
            segs = out.line + out.why + out.fit
            ids = sorted({refs[i.strip().upper()].claim_id for s in segs for i in s.claim_ids if i.strip().upper() in refs})
            return FounderWritten(join(out.line), join(out.why), join(out.fit), ids, "llm", attempt)
    log.info("founder lines fell back to template for %s: %s", name, errors)
    w = founder_template(name, refs, labels)
    w.attempts = MAX_ATTEMPTS
    return w


# --- curated left out ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    key: Any
    reason_code: str
    would_have_ranked: int | None
    region: str


def pick_curated(rows: list[Candidate]) -> list[tuple[str, Candidate]]:
    """One per category: the best-ranked India-located exclusion, else the best-ranked overall. Pure."""
    out = []
    for cat, codes, _ in CATEGORIES:
        pool = sorted((r for r in rows if r.reason_code in codes), key=lambda r: (r.would_have_ranked is None, r.would_have_ranked or 0))
        best = next((r for r in pool if r.region == "india"), None) or (pool[0] if pool else None)
        if best is not None:
            out.append((cat, best))
    return out


async def curated_line(gemini: Any, company: str, reason_code: str, explanation: str, claims: list[Any]) -> Written:
    refs = build_refs([c for c in claims if c.entity_type == "company"][:25], {c.entity_id: company for c in claims})
    reason = f"{company} was left out ({reason_code}): {explanation}"
    template = Written(re.sub(r"\s+", " ", f"{explanation.rstrip('.')}." + (
        f" {next(iter(r.statement for r in refs.values() if r.field == 'description'), '')}" if refs else "")).strip(),
        [], "template", 0)
    if gemini is None:
        return template
    line, attempts, errors = await _write(gemini, CURATED_SYSTEM, f"COMPANY: {company}\nR: {reason}", refs,
                                          CURATED_MAX_WORDS, extra={"R": reason})
    if line is None:
        log.info("curated line fell back to template for %s: %s", company, errors)
        template.attempts = attempts
        return template
    ids = sorted({refs[i.strip().upper()].claim_id for s in line.segments for i in s.claim_ids if i.strip().upper() in refs})
    return Written(join(line.segments), ids, "llm", attempts)
