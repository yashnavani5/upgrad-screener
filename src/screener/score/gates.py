"""Gates (SPEC 3.7) and exclusions (config/exclusions.yaml) with reason codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from screener.resolve.normalize import normalize_name, registered_domain
from screener.score.features import CompanyData, headcount_value, usable
from screener.scouts.prefilter import Prefilter
from screener.urls import host_of

# Order matters: the first failing check is the primary reason shown in "Left out".
ORDER = ("UPGRAD_OWNED", "COMPETITOR", "ACQUIRED", "TOO_LARGE", "NOT_EDUCATION", "INACTIVE", "UNVERIFIABLE",
         "POLICY_FILTER")
EXISTENCE_FIELDS = ("website", "legal_name", "incorporation_date")
SIGNAL_FIELDS = ("prior_exit", "prior_role", "funding_round", "revenue", "acquired", "open_roles",
                 "latest_news_date", "github_stars", "github_last_push", "education")


@dataclass(frozen=True)
class GateResult:
    passed: bool              # scoring gates (education, active, identity, exclusions)
    shortlist_ok: bool        # rule 2: identity + current role + existence + >= N signal claims
    reason_code: str | None
    explanation: str
    source_claim_id: Any = None


def on_list(cd: Any, names: list[str]) -> bool:
    """True when any of the company's names (aliases) matches a listed name (normalized, as for the blocklist)."""
    wanted = {normalize_name(n) for n in names if n}
    return bool(wanted) and bool({normalize_name(n) for n in cd.aliases if n} & wanted)


def policy_filter(s: Any, filters: dict[str, Any], blocklist: list[str]) -> str | None:
    """The AI Policy's extra filters, applied after scoring (they need the computed score and features).
    Returns the reason text when the company is excluded, else None. Pure."""
    cd = s.cd
    if on_list(cd, blocklist):
        return "On the policy blocklist."
    allowed = filters.get("regions_allowed") or {}
    if allowed and not allowed.get(cd.geo, True):
        return f"Region '{cd.geo}' is not allowed by the policy."
    year = getattr(cd, "founded_year", None)
    if filters.get("founded_after") and year and year < int(filters["founded_after"]):
        return f"Founded in {year}, before the policy's {filters['founded_after']} cutoff."
    if filters.get("founded_before") and year and year > int(filters["founded_before"]):
        return f"Founded in {year}, after the policy's {filters['founded_before']} cutoff."
    raised = getattr(cd, "raised_usd", None)
    if filters.get("require_verified_funding") and not raised:
        return "No verified funding (the policy requires it)."
    if filters.get("min_total_raised_usd") and (raised or 0) < float(filters["min_total_raised_usd"]):
        return f"Verified funding below the policy minimum of ${float(filters['min_total_raised_usd']):,.0f}."
    stages = {x.lower() for x in getattr(cd, "stages", [])}
    if blocked := stages & set(filters.get("excluded_stages") or []):
        return f"Funding stage excluded by the policy: {', '.join(sorted(blocked))}."
    if s.c < float(filters.get("min_confidence") or 0):
        return f"Confidence {s.c:.2f} below the policy minimum of {float(filters['min_confidence']):.2f}."
    if s.final_prime < float(filters.get("min_final_score") or 0):
        return f"Final score {s.final_prime:.3f} below the policy minimum of {float(filters['min_final_score']):.3f}."
    if cd.verified_claims < int(filters.get("min_verified_claims") or 0):
        return f"{cd.verified_claims} verified claims, below the policy minimum of {filters['min_verified_claims']}."
    return None


def _non_linkedin(url: str) -> bool:
    """Not a self-published social profile (LinkedIn, X)."""
    host = host_of(url)
    return not any(host == h or host.endswith("." + h) for h in ("linkedin.com", "lnkd.in", "x.com", "twitter.com"))


def evaluate(cd: CompanyData, exclusions: dict[str, Any], prefilter: Prefilter, min_signals: int,
             filters: dict[str, Any] | None = None) -> GateResult:
    """`filters`: the AI Policy switches (policy.Filters); missing keys keep the default (on)."""
    on = lambda key: (filters or {}).get(key, True)  # noqa: E731
    names = {normalize_name(n) for n in cd.aliases if n}
    fails: dict[str, tuple[str, Any]] = {}

    upgrad = [normalize_name(n) for n in exclusions.get("upgrad_owned") or []] + [
        normalize_name(n) for n in exclusions.get("self_names") or []]
    if on("exclude_upgrad_owned") and any(n == u or n.startswith(u + " ") for n in names for u in upgrad if u):
        fails["UPGRAD_OWNED"] = (f"{cd.company.name} is upGrad or an upGrad-owned brand.", None)
    competitors = {normalize_name(n) for n in exclusions.get("competitors_at_scale") or []}
    if on("exclude_competitors") and names & competitors:
        fails["COMPETITOR"] = (f"{cd.company.name} is on the competitors-at-scale list.", None)

    # Acquired: no longer an independent company (one supported source is enough to exclude, as for size).
    acquired = next((c for c in getattr(cd, "all_claims", cd.claims) if c.field == "acquired"
                     and getattr(c, "verified_status", "supported") == "supported"
                     and getattr(c, "entity_type", "company") == "company"), None)
    if acquired is not None and on("exclude_acquired"):
        v = acquired.value_json or {}
        when = f" ({v['date']})" if v.get("date") else ""
        fails["ACQUIRED"] = (f"Acquired by {v.get('acquirer') or 'another company'}{when}: no longer an independent company.",
                             acquired.id)

    ceiling = exclusions.get("size_ceiling") or {}
    raised, heads = getattr(cd, "raised_usd", None) or cd.company.total_raised_usd, headcount_value(cd)
    basis = getattr(cd, "raised_basis", None) or "corroborated funding rounds"
    if raised is not None and raised > float(ceiling.get("max_total_raised_usd", float("inf"))):
        field_ = "total_funding" if basis.startswith("total funding") else "funding_round"
        src = next((c.id for c in getattr(cd, "all_claims", cd.claims) if c.field == field_
                    and getattr(c, "verified_status", "supported") == "supported"), None)
        fails["TOO_LARGE"] = (f"Raised ${raised:,.0f} ({basis}), above the ${ceiling['max_total_raised_usd']:,.0f} ceiling.", src)
    elif heads is not None and heads > int(ceiling.get("max_headcount", 10**9)):
        src = next((c.id for c in cd.claims if c.field == "headcount"), None)
        fails["TOO_LARGE"] = (f"Headcount {heads:,} exceeds the {ceiling['max_headcount']:,} ceiling.", src)

    desc = " ".join(str(c.value_json.get("text") or "") for c in cd.claims if c.field == "description")
    if on("require_education") and not (cd.segments or cd.capabilities or prefilter.matches(desc)):
        fails["NOT_EDUCATION"] = ("No verified claim shows an education business (no segment/capability tag, "
                                  "and the verified description has no education terms).", None)

    if on("exclude_inactive") and cd.company.status == "inactive":
        fails["INACTIVE"] = (f"Liveness: {cd.company.status_reason or 'inactive'}.", None)

    identified = [f for f in cd.founders if f.founder.identity_verified]
    if not identified and on("require_identity_verified"):
        fails["UNVERIFIABLE"] = ("No founder identity verified by a non-LinkedIn source.", None)

    passed = not fails
    # Shortlist gate (non-negotiable 2): identity + current role + company existence + >= N signal claims.
    # Company existence: a supported website/registry claim, or any supported claim from the company's own site.
    domain = cd.company.domain
    exists = any(c.field in EXISTENCE_FIELDS or (domain and registered_domain(c.source_url) == domain) for c in cd.claims)
    role = any(usable(c) and c.field == "current_role" and _non_linkedin(c.source_url) for f in identified for c in f.claims)
    signals = sum(1 for c in cd.claims if c.field in SIGNAL_FIELDS) + sum(
        1 for f in identified for c in f.claims if usable(c) and c.field in SIGNAL_FIELDS)
    exists = exists or not on("require_company_existence")
    role = role or not on("require_current_role")
    shortlist_ok = passed and exists and role and signals >= min_signals
    if passed and not shortlist_ok:
        missing = [m for m, ok in (("company existence", exists), ("current role", role),
                                   (f">= {min_signals} verified track-record/interest claims (has {signals})", signals >= min_signals)) if not ok]
        fails["UNVERIFIABLE"] = ("Scored, but not shortlist-eligible: missing " + ", ".join(missing) + ".", None)
    code = next((c for c in ORDER if c in fails), None)
    text, src = fails[code] if code else ("Passed all gates.", None)
    return GateResult(passed, shortlist_ok, code, text, src)
