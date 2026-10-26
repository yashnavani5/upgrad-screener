"""Grounding checks for LLM extractions: the evidence span must be copied from the source text, and every
extracted name must appear in it. Anything that fails is dropped (never repaired or guessed)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

SPAN_THRESHOLD = 90
NAME_THRESHOLD = 90
MIN_SPAN_CHARS = 12
SHORT_NAME_CHARS = 5

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "\u2013": "-", "\u2014": "-",
    "−": "-", " ": " ", "…": "...",
})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE)
    return re.sub(r"\s+", " ", text).strip().casefold()


NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text)}


def span_in_source(span: str | None, source: str, threshold: int = SPAN_THRESHOLD) -> bool:
    """True if `span` (after normalizing case, quotes, dashes, whitespace) occurs in `source`
    with rapidfuzz partial_ratio >= threshold AND every number in the span appears exactly in the source
    (fuzzy matching alone would accept 'Rs 400 crore' against 'Rs 40 crore')."""
    s, src = normalize(span or ""), normalize(source)
    if len(s) < MIN_SPAN_CHARS or not src:
        return False
    if s in src:
        return True
    if not _numbers(s) <= _numbers(src):
        return False
    return fuzz.partial_ratio(s, src) >= threshold


def name_in_text(name: str | None, text: str, threshold: int = NAME_THRESHOLD) -> bool:
    """Short names need an exact word-boundary match; longer ones may differ slightly (partial_ratio)."""
    n, t = normalize(name or ""), normalize(text)
    if not n or not t:
        return False
    if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", t):
        return True
    if len(n) < SHORT_NAME_CHARS:
        return False
    return fuzz.partial_ratio(n, t) >= threshold


@dataclass(frozen=True)
class Grounding:
    ok: bool
    reason: str


def check(span: str | None, source: str, name: str | None) -> Grounding:
    if not span_in_source(span, source):
        return Grounding(False, "evidence_span not found in source text")
    if name and not name_in_text(name, span or ""):
        return Grounding(False, "name not inside evidence_span")
    return Grounding(True, "grounded")
