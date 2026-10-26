"""Pure verification rules: corroboration, liveness and identity. No I/O, fully unit-tested."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from screener.enrich.money import same_amount, year_of
from screener.resolve.matching import jaro_winkler
from screener.resolve.normalize import domain_label, normalize_name, normalize_person, registered_domain
from screener.urls import host_of


@dataclass(frozen=True)
class ClaimView:
    id: Any
    entity_id: Any
    field: str
    value: dict[str, Any]
    source_url: str
    status: str
    confidence: float | None = None


# --- corroboration ---------------------------------------------------------------------------------


def is_primary(source_url: str, company_domain: str | None, acquirers: list[str], conf: dict[str, Any]) -> bool:
    """Primary source: the company's own site, an acquirer's site, or a government filing/registry."""
    domain = registered_domain(source_url)
    host = host_of(source_url)
    if not domain:
        return False
    if company_domain and domain == company_domain:
        return True
    if any(host == s or host.endswith("." + s) for s in conf.get("government_suffixes", [])):
        return True
    if domain in conf.get("primary_domains", []):
        return True
    label = (domain_label(domain) or "").replace("-", "")
    for acquirer in acquirers:
        a = normalize_name(acquirer).replace(" ", "")
        if a and label and (a == label or jaro_winkler(a, label) >= 0.92):
            return True
    return False


def _stage(v: dict[str, Any]) -> str:
    return re.sub(r"[^a-z0-9]", "", (v.get("stage") or "").lower())  # "Series-A" == "series a"


def _fy(v: dict[str, Any]) -> str | None:
    """'FY24', 'FY2023-24', 'FY 2024' -> '2024' (the fiscal year's ending year)."""
    text = (v.get("fiscal_year") or "").upper()
    m = re.search(r"(\d{4})\s*[-/]\s*(\d{2,4})", text)
    if m:
        end = m.group(2)
        return end if len(end) == 4 else m.group(1)[:2] + end
    m = re.search(r"(\d{4}|\d{2})", text)
    if not m:
        return None
    y = m.group(1)
    return y if len(y) == 4 else "20" + y


def equivalent(a: ClaimView, b: ClaimView, tolerance: float) -> bool:
    """Do two claims state the same fact (for corroboration)?"""
    if a.field != b.field or a.entity_id != b.entity_id:
        return False
    va, vb = a.value, b.value
    if a.field == "funding_round":
        amt_a, amt_b = va.get("amount_usd"), vb.get("amount_usd")
        if amt_a is not None and amt_b is not None:
            if not same_amount(amt_a, amt_b, tolerance):
                return False
            sa, sb = _stage(va), _stage(vb)
            return not (sa and sb and sa != sb)
        sa, sb = _stage(va), _stage(vb)
        ya, yb = year_of(va.get("date")), year_of(vb.get("date"))
        return bool(sa and sa == sb and ya and ya == yb)
    if a.field == "revenue":
        return _fy(va) is not None and _fy(va) == _fy(vb) and same_amount(va.get("amount_usd") or va.get("absolute_original"),
                                                                          vb.get("amount_usd") or vb.get("absolute_original"), tolerance)
    if a.field == "acquired":
        return same_org(va.get("acquirer"), vb.get("acquirer"))
    if a.field == "prior_exit":
        return same_org(va.get("company"), vb.get("company"))
    return a.value == b.value


def same_org(a: str | None, b: str | None) -> bool:
    """'Toppr' ~ 'Toppr Technologies': equal, near-equal, or one name's words are a subset of the other's."""
    x, y = normalize_name(a or ""), normalize_name(b or "")
    if not x or not y:
        return False
    tx, ty = set(x.split()), set(y.split())
    return x == y or jaro_winkler(x, y) >= 0.92 or tx <= ty or ty <= tx


@dataclass(frozen=True)
class Corroboration:
    needed: bool
    corroborated: bool
    domains: tuple[str, ...] = ()
    primary: bool = False
    supporting_ids: tuple[Any, ...] = ()


def corroborate(claims: list[ClaimView], company_domain: str | None, conf: dict[str, Any]) -> dict[Any, Corroboration]:
    """Exits, funding amounts and revenue need 2 independent domains OR 1 primary source, counting only
    supported claims that state the same fact. Other fields don't need corroboration."""
    fields = set(conf.get("corroboration_fields", []))
    tol = float(conf.get("amount_tolerance", 0.10))
    supported = [c for c in claims if c.status == "supported"]
    out: dict[Any, Corroboration] = {}
    for c in claims:
        if c.field not in fields:
            out[c.id] = Corroboration(needed=False, corroborated=True)
            continue
        if c.status != "supported":
            out[c.id] = Corroboration(needed=True, corroborated=False)
            continue
        group = [o for o in supported if o.id == c.id or equivalent(c, o, tol)]
        domains = tuple(sorted({d for o in group if (d := registered_domain(o.source_url))}))
        acquirers = [o.value.get("acquirer") for o in group if o.value.get("acquirer")]
        primary = any(is_primary(o.source_url, company_domain, acquirers, conf) for o in group)
        out[c.id] = Corroboration(True, len(domains) >= 2 or primary, domains, primary, tuple(o.id for o in group))
    return out


# --- liveness --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LivenessInput:
    website_resolves: bool | None
    latest_activity: date | None
    founder_listed: bool | None
    today: date
    months: int = 12


def months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month) - (1 if later.day < earlier.day else 0)


def liveness(inp: LivenessInput) -> tuple[str, str]:
    """INACTIVE only on strong, combined evidence: the website is dead AND there is no activity within `months`,
    or the founder is no longer listed AND the latest activity is older than `months`. One weak signal alone
    (an old news date, one failed fetch) gives UNKNOWN, which lowers confidence instead of excluding.
    ACTIVE when the website resolves and there is activity within `months`."""
    recent = inp.latest_activity is not None and months_between(inp.latest_activity, inp.today) <= inp.months
    stale = inp.latest_activity is not None and not recent
    activity = (f"activity on {inp.latest_activity.isoformat()}" if recent else
                f"no dated activity since {inp.latest_activity.isoformat()}" if stale else "no dated activity found")
    site = {None: "website not checked", True: "website resolves", False: "website does not resolve"}[inp.website_resolves]
    founder = {None: "founder listing unknown", True: "founder listed", False: "founder no longer listed"}[inp.founder_listed]
    if inp.website_resolves is False and not recent:
        return "inactive", f"{site} and {activity}"
    if inp.founder_listed is False and stale:
        return "inactive", f"{founder} and {activity}"
    if inp.website_resolves and recent:
        return "active", "; ".join((site, activity, founder))
    return "unknown", "; ".join((site, activity, founder))


def parse_date(text: str | None) -> date | None:
    """'2026-03-14', '2026-03', '2026' -> date (start of the stated period: conservative for recency)."""
    if not text:
        return None
    m = re.match(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(text))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


# --- identity --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityInput:
    founder_name: str
    company_names: list[str] = field(default_factory=list)
    founder_claims: list[ClaimView] = field(default_factory=list)   # claims about this founder
    company_claims: list[ClaimView] = field(default_factory=list)   # claims about the company (e.g. directors)


SOCIAL_HOSTS = ("linkedin.com", "lnkd.in", "x.com", "twitter.com")


def _non_linkedin(url: str) -> bool:
    """Independent of self-published social profiles (LinkedIn, X): those never verify identity on their own."""
    host = host_of(url)
    return not any(host == h or host.endswith("." + h) for h in SOCIAL_HOSTS)


def identity_verified(inp: IdentityInput) -> bool:
    """Founder name + company linked by >= 1 supported claim from a non-LinkedIn source."""
    names = {normalize_name(n) for n in inp.company_names if n}
    for c in inp.founder_claims:
        if c.status == "supported" and c.field == "current_role" and _non_linkedin(c.source_url):
            company = normalize_name(c.value.get("company") or "")
            if company in names or any(jaro_winkler(company, n) >= 0.92 for n in names):
                return True
    person = normalize_person(inp.founder_name)
    for c in inp.company_claims:
        if c.status == "supported" and c.field == "director" and _non_linkedin(c.source_url):
            if normalize_person(c.value.get("name") or "") == person:
                return True
    return False
