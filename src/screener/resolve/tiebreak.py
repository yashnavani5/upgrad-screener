"""Gemini-fast tiebreak for ambiguous company pairs: same or different, with a reason."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from screener.resolve.matching import PairFeatures, Record

SYSTEM = (
    "You decide whether two records refer to the SAME company. Use only the information given. Different "
    "products that merely share a word in their name are different companies. If the evidence is not enough "
    "to be confident they are the same, answer same=false. Keep the reason to one sentence."
)


class TiebreakAnswer(BaseModel):
    same: bool = Field(description="True only if both records clearly describe the same company.")
    reason: str


def _describe(label: str, r: Record) -> str:
    return (
        f"{label}:\n  name: {r.name}\n  other names: {', '.join(sorted(set(r.raw_names))[:5])}\n"
        f"  domain: {r.domain or 'unknown'}\n  founders: {', '.join(sorted(r.founders)) or 'unknown'}\n"
        f"  city: {r.city or 'unknown'}\n  found via: {', '.join(sorted(r.scouts))}\n"
        f"  description: {(r.description or 'none')[:500]}\n"
    )


async def tiebreak(gemini: Any, a: Record, b: Record, f: PairFeatures) -> TiebreakAnswer:
    prompt = (
        _describe("RECORD A", a) + "\n" + _describe("RECORD B", b)
        + f"\nName similarity (Jaro-Winkler): {f.jw:.2f}. Are A and B the same company?"
    )
    return await gemini.generate_json(prompt, TiebreakAnswer, "fast", system=SYSTEM)
