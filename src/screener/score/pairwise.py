"""Pairwise judge (Gemini-smart) over the top N, both orders, then Bradley-Terry via choix.

The judge sees two dossiers built from verified claims only plus the gap map, and returns {winner, reason}.
It compares; it never produces a numeric score (code fits BT and blends per SPEC)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import choix
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SYSTEM = (
    "You help upGrad's partnerships / M&A / co-build team compare two education companies. Use ONLY the "
    "verified facts in the two dossiers and upGrad's gap map. Pick the company that is the stronger opportunity "
    "for upGrad (founder track record, momentum, novelty, fit with the gap map). Do not invent facts; if a "
    "dossier lacks evidence, that counts against it. Answer with the winner ('A' or 'B') and one-sentence reason."
)


class Judgment(BaseModel):
    winner: Literal["A", "B"]
    reason: str = Field(description="One sentence, citing facts from the dossiers.")


@dataclass
class PairResult:
    a: Any
    b: Any
    winner_ab: str | None      # judgment with a shown as A
    winner_ba: str | None      # judgment with b shown as A
    reason_ab: str = ""
    reason_ba: str = ""

    @property
    def outcome(self) -> Any | None:
        """a, b, or None (tie: the two orders disagree, or a call failed)."""
        first = {"A": self.a, "B": self.b}.get(self.winner_ab or "")
        second = {"A": self.b, "B": self.a}.get(self.winner_ba or "")
        return first if first is not None and first == second else None


def neighbour_pairs(order: list[Any], window: int) -> set[tuple[Any, Any]]:
    """Round 1: each item vs the next `window` items in the current ranking."""
    pairs = set()
    for i, x in enumerate(order):
        for y in order[i + 1 : i + 1 + window]:
            pairs.add((x, y))
    return pairs


def closest_pairs(strengths: dict[Any, float], per_item: int, done: set[frozenset[Any]]) -> set[tuple[Any, Any]]:
    """Round 2 (adaptive): pair items with the closest current BT strengths, skipping pairs already judged,
    until each item has about `per_item` comparisons in total."""
    counts = {x: sum(1 for p in done if x in p) for x in strengths}
    items = sorted(strengths, key=lambda x: strengths[x])
    out: set[tuple[Any, Any]] = set()
    for i, x in enumerate(items):
        for y in sorted(items[:i] + items[i + 1 :], key=lambda y: abs(strengths[y] - strengths[x])):
            if counts[x] >= per_item:
                break
            pair = frozenset((x, y))
            if pair in done or counts[y] >= per_item + 2:
                continue
            done = done | {pair}
            out.add((x, y))
            counts[x] += 1
            counts[y] += 1
    return out


def fit_bradley_terry(items: list[Any], results: list[PairResult], alpha: float) -> dict[Any, float]:
    """BT strengths with choix ILSR. A tie counts as one win each way."""
    index = {x: i for i, x in enumerate(items)}
    data: list[tuple[int, int]] = []
    for r in results:
        w = r.outcome
        if w is None:
            data += [(index[r.a], index[r.b]), (index[r.b], index[r.a])]
        else:
            loser = r.b if w == r.a else r.a
            data.append((index[w], index[loser]))
    if not data:
        return {x: 0.0 for x in items}
    params = choix.ilsr_pairwise(len(items), data, alpha=alpha)
    return {x: float(params[index[x]]) for x in items}


async def judge_pair(gemini: Any, a: Any, b: Any, dossier: Callable[[Any], str], gap_map_text: str) -> PairResult:
    async def ask(first: Any, second: Any) -> Judgment | None:
        prompt = (f"UPGRAD GAP MAP:\n{gap_map_text}\n\nDOSSIER A:\n{dossier(first)}\n\nDOSSIER B:\n{dossier(second)}\n\n"
                  "Which is the stronger opportunity for upGrad?")
        try:
            return await gemini.generate_json(prompt, Judgment, "smart", system=SYSTEM)  # cached by prompt
        except Exception as exc:
            log.warning("pairwise judgment failed: %s", exc)
            return None

    ab, ba = await asyncio.gather(ask(a, b), ask(b, a))
    return PairResult(a, b, ab.winner if ab else None, ba.winner if ba else None,
                      ab.reason if ab else "", ba.reason if ba else "")


async def run_pairwise(gemini: Any, order: list[Any], dossier: Callable[[Any], str], gap_map_text: str,
                       conf: dict[str, Any], concurrency: int = 6) -> tuple[dict[Any, float], list[PairResult]]:
    """Adaptive comparisons over `order` (already the top N by Final), both orders, then BT strengths."""
    sem = asyncio.Semaphore(concurrency)
    per_item = int(conf.get("comparisons_per_item", 10))
    alpha = float(conf.get("alpha", 0.01))
    results: list[PairResult] = []

    async def judge(pairs: set[tuple[Any, Any]]) -> None:
        async def one(p: tuple[Any, Any]) -> PairResult:
            async with sem:
                return await judge_pair(gemini, p[0], p[1], dossier, gap_map_text)

        results.extend(await asyncio.gather(*(one(p) for p in sorted(pairs, key=str))))

    if len(order) < 2:
        return {x: 0.0 for x in order}, results
    await judge(neighbour_pairs(order, int(conf.get("neighbour_window", 3))))
    strengths = fit_bradley_terry(order, results, alpha)
    done = {frozenset((r.a, r.b)) for r in results}
    await judge(closest_pairs(strengths, per_item, done))
    return fit_bradley_terry(order, results, alpha), results

