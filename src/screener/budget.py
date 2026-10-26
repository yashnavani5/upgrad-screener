"""Budget guard: size each stage so a run stops gracefully before a limit is exceeded.

Limits: the per-run Gemini call cap (RUN_MAX_LLM_CALLS), an optional daily Gemini call cap
(`providers.yaml budget.gemini_daily_limit`, counted as 'gemini:calls' per UTC day) and the USD spend caps
(RUN_BUDGET_USD per run; `budget.gemini_daily_usd_limit` / `gemini_monthly_usd_limit` shared by every process,
counted as 'gemini:usd' per UTC day and per UTC month). The USD caps are enforced by GeminiClient itself;
the guard only uses them to size stages so a run winds down instead of hitting the wall.
Paid search providers are already capped per month by the search router (they are skipped, never exceeded)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

DAILY_KEY = "gemini:calls"
SPEND_KEY = "gemini:usd"


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


@dataclass
class BudgetGuard:
    run_cap: int
    run_used: Any                 # callable -> calls used by this process
    daily_cap: int | None = None
    daily_used: Any = None        # callable -> calls used today (all processes)
    reserve: int = 0
    estimates: dict[str, float] | None = None
    usd_left: Any = None          # callable -> USD left under the tightest spend cap, or None (no USD cap)

    def remaining(self) -> int:
        left = self.run_cap - int(self.run_used())
        if self.daily_cap is not None and self.daily_used is not None:
            left = min(left, self.daily_cap - int(self.daily_used()))
        usd = self.usd_left() if self.usd_left is not None else None
        if usd is not None:
            per_call = float((self.estimates or {}).get("usd_per_call", 0.005))
            left = min(left, math.floor(usd / per_call) if per_call > 0 else left)
        return max(0, left)

    def exhausted(self) -> bool:
        return self.remaining() <= 0

    def affordable(self, units: int, per_unit_key: str, *, keep_reserve: bool = True) -> int:
        """How many of `units` fit in the remaining budget (minus the reserve for later stages)."""
        per = float((self.estimates or {}).get(per_unit_key, 1.0))
        left = self.remaining() - (self.reserve if keep_reserve else 0)
        if per <= 0:
            return units
        return max(0, min(units, math.floor(left / per)))

    def fits(self, units: int, per_unit_key: str, *, keep_reserve: bool = False) -> bool:
        return self.affordable(units, per_unit_key, keep_reserve=keep_reserve) >= units


def from_config(cfg: Any, gemini: Any, quota: Any) -> BudgetGuard:
    conf = cfg.providers.get("budget", {})
    daily_cap = conf.get("gemini_daily_limit")
    return BudgetGuard(
        run_cap=int(cfg.settings.run_max_llm_calls),
        run_used=lambda: getattr(gemini, "calls", 0),
        daily_cap=int(daily_cap) if daily_cap is not None else None,
        daily_used=(lambda: quota.used(DAILY_KEY, today())) if quota is not None else None,
        reserve=int(conf.get("reserve_calls", 0)),
        estimates=conf.get("estimates") or {},
        usd_left=getattr(gemini, "usd_remaining", None),
    )
