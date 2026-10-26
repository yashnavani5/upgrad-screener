"""Money parsing and conversion. INR converts to USD at the configured rate; the original is always kept."""

from __future__ import annotations

import re
from typing import Any

UNIT_MULTIPLIERS = {
    None: 1.0, "": 1.0, "none": 1.0, "units": 1.0,
    "thousand": 1e3, "k": 1e3,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "million": 1e6, "mn": 1e6, "m": 1e6, "mln": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
}
CURRENCY_ALIASES = {
    "inr": "INR", "rs": "INR", "rs.": "INR", "rupee": "INR", "rupees": "INR", "₹": "INR",
    "usd": "USD", "$": "USD", "us$": "USD", "dollar": "USD", "dollars": "USD",
}


def normalize_currency(currency: str | None) -> str | None:
    if not currency:
        return None
    c = currency.strip().lower()
    return CURRENCY_ALIASES.get(c, currency.strip().upper() if len(c) == 3 else None)


def to_absolute(amount: float | None, unit: str | None) -> float | None:
    if amount is None:
        return None
    key = (unit or "").strip().lower().rstrip(".")
    if key not in UNIT_MULTIPLIERS:
        return None
    return float(amount) * UNIT_MULTIPLIERS[key]


def to_usd(amount: float | None, currency: str | None, unit: str | None, inr_per_usd: float) -> dict[str, Any] | None:
    """{'amount_usd', 'amount_original', 'unit_original', 'currency_original', 'absolute_original'} or None
    if the amount, unit or currency can't be interpreted (never guessed)."""
    cur = normalize_currency(currency)
    absolute = to_absolute(amount, unit)
    if absolute is None or cur not in ("INR", "USD"):
        return None
    usd = absolute if cur == "USD" else absolute / inr_per_usd
    return {
        "amount_usd": round(usd, 2),
        "amount_original": amount,
        "unit_original": unit,
        "currency_original": cur,
        "absolute_original": absolute,
    }


def same_amount(a: float | None, b: float | None, tolerance: float) -> bool:
    if a is None or b is None or a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= tolerance


YEAR_RE = re.compile(r"(19|20)\d{2}")


def year_of(date_text: str | None) -> int | None:
    m = YEAR_RE.search(date_text or "")
    return int(m.group(0)) if m else None
