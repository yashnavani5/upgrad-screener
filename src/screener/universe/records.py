"""Pure helpers for registry rows: CIN parsing, field mapping, normalization, validation and triage scoring."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from screener.resolve.matching import jaro_winkler
from screener.resolve.normalize import normalize_name, registered_domain
from screener.scouts.prefilter import Prefilter


# --- CIN ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cin:
    listed: bool
    nic: str          # 5-digit industry code (NIC) embedded in the CIN
    state: str
    year: int
    company_type: str


def parse_cin(cin: str | None, pattern: str) -> Cin | None:
    """'U80903KA2011PTC061427' -> Cin(listed=False, nic='80903', state='KA', year=2011, type='PTC')."""
    c = (cin or "").strip().upper()
    if not re.match(pattern, c):
        return None
    return Cin(c[0] == "L", c[1:6], c[6:8], int(c[8:12]), c[12:15])


def is_education_nic(nic: str | None, prefixes: list[str]) -> bool:
    return bool(nic) and any(nic.startswith(p) for p in prefixes)


# --- field mapping / normalization -------------------------------------------------------------------


def _col(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def pick(row: dict[str, Any], candidates: list[str]) -> str | None:
    """First non-empty value among candidate column names (case- and separator-insensitive:
    'CompanyName', 'company_name' and 'Company Name' are the same column)."""
    norm = {_col(k): v for k, v in row.items()}
    for cand in candidates:
        v = norm.get(_col(cand))
        if v is not None and str(v).strip() not in ("", "NA", "N/A", "null", "None", "-"):
            return str(v).strip()
    return None


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text.strip()[:11], fmt).date()
        except ValueError:
            continue
    return None


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(re.sub(r"[^\d.]", "", text) or "nan")
    except ValueError:
        return None


@dataclass
class RegistryRow:
    source: str
    source_id: str
    name: str
    cin: str | None = None
    nic_code: str | None = None
    status: str | None = None
    registered_on: date | None = None
    paid_up_capital_inr: float | None = None
    state: str | None = None
    city: str | None = None
    activity: str | None = None
    sector: str | None = None
    website: str | None = None
    aliases: list[str] = field(default_factory=list)   # e.g. DPIIT legal name
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_name(self) -> str:
        return normalize_name(self.name)

    def content_hash(self) -> str:
        payload = [self.name, self.cin, self.status, str(self.registered_on), self.paid_up_capital_inr, self.state,
                   self.city, self.activity, self.sector, self.website]
        return hashlib.sha256(json.dumps(payload, default=str).encode()).hexdigest()


def mca_row(raw: dict[str, Any], fields: dict[str, list[str]], cin_pattern: str) -> RegistryRow | None:
    cin = (pick(raw, fields["cin"]) or "").upper()
    name = pick(raw, fields["name"])
    if not cin or not name:
        return None
    parsed = parse_cin(cin, cin_pattern)
    return RegistryRow(
        source="mca", source_id=cin, name=name, cin=cin, nic_code=parsed.nic if parsed else None,
        status=pick(raw, fields["status"]), registered_on=parse_date(pick(raw, fields["registered_on"])),
        paid_up_capital_inr=parse_number(pick(raw, fields["paid_up_capital"])), state=pick(raw, fields["state"]),
        activity=pick(raw, fields["activity"]), raw=raw,
    )


def dpiit_row(raw: dict[str, Any], fields: dict[str, list[str]], cin_pattern: str) -> RegistryRow | None:
    name = pick(raw, fields["name"]) or pick(raw, fields["legal_name"])
    if not name:
        return None
    cin = (pick(raw, fields["cin"]) or "").upper() or None
    parsed = parse_cin(cin, cin_pattern) if cin else None
    sector = " | ".join(x for x in (pick(raw, fields["industry"]), pick(raw, fields["sector"]), pick(raw, fields["services"])) if x)
    source_id = pick(raw, fields["recognition_id"]) or cin or f"{normalize_name(name)}|{(pick(raw, fields['state']) or '').lower()}"
    website = pick(raw, fields["website"])
    if website and "://" not in website:
        website = "https://" + website
    return RegistryRow(
        source="dpiit", source_id=source_id, name=name, cin=cin, nic_code=parsed.nic if parsed else None,
        status=pick(raw, fields["status"]), state=pick(raw, fields["state"]), city=pick(raw, fields["city"]),
        sector=sector or None, website=website if website and registered_domain(website) else None,
        aliases=[x for x in [pick(raw, fields["legal_name"])] if x], raw=raw,
    )


# --- validation --------------------------------------------------------------------------------------


def validate_row(row: RegistryRow, conf: dict[str, Any]) -> tuple[str, list[str]]:
    """Row-level checks. 'invalid' rows are never crawled; 'warning' rows are crawled with lower priority."""
    issues: list[str] = []
    if not row.name or len(row.name.strip()) < 2:
        issues.append("invalid:empty_name")
    if row.cin and not (re.match(conf["cin_pattern"], row.cin) or re.match(conf["llpin_pattern"], row.cin)):
        issues.append("invalid:cin_format" if row.source == "mca" else "warning:cin_format")
    if row.source == "mca" and row.registered_on is None:
        issues.append("warning:no_registration_date")
    if row.registered_on and row.registered_on > date.today():
        issues.append("invalid:registration_in_future")
    parsed = parse_cin(row.cin, conf["cin_pattern"]) if row.cin else None
    if parsed and row.registered_on and abs(parsed.year - row.registered_on.year) > 1:
        issues.append("warning:cin_year_mismatch")
    if row.source == "dpiit" and row.website is None and row.raw:
        issues.append("warning:no_website")
    status = "invalid" if any(i.startswith("invalid:") for i in issues) else ("warning" if issues else "valid")
    return status, issues


def cross_validate(dpiit: RegistryRow, mca: RegistryRow | None, conf: dict[str, Any]) -> list[str]:
    """DPIIT row vs the MCA row with the same CIN: name and state must agree."""
    if mca is None:
        return ["warning:cin_not_in_mca"] if dpiit.cin else ["warning:no_cin"]
    issues = []
    names = [dpiit.name, *dpiit.aliases]
    if max(jaro_winkler(normalize_name(n), mca.normalized_name) for n in names if n) < float(conf["name_match_jw"]):
        issues.append("warning:name_mismatch_with_mca")
    if dpiit.state and mca.state and dpiit.state.strip().lower()[:4] != mca.state.strip().lower()[:4]:
        issues.append("warning:state_mismatch_with_mca")
    return issues or ["ok:cross_validated_with_mca"]


def status_class(status: str | None, conf: dict[str, Any]) -> str:
    s = (status or "").strip().lower()
    if not s:
        return "unknown"
    if any(x in s for x in conf["inactive_statuses"]):
        return "inactive"
    if any(s == x or s.startswith(x) for x in conf["active_statuses"]):
        return "active"
    return "unknown"


# --- triage ------------------------------------------------------------------------------------------


def name_is_educational(name: str, edu: dict[str, Any], prefilter: Prefilter) -> bool:
    """Registry-name check: education words, word prefixes (edu-, learn-, vidya-...) or the general prefilter."""
    if prefilter.matches(name) or Prefilter(edu.get("name_terms") or ["education"]).matches(name):
        return True
    tokens = re.findall(r"[a-z]+", (name or "").lower())
    return any(tok.startswith(p) for tok in tokens for p in edu.get("name_prefixes") or [])


@dataclass(frozen=True)
class TriageResult:
    score: float
    reasons: list[str]
    crawl: bool


def triage(rows: list[RegistryRow], issues: list[str], universe: dict[str, Any], prefilter: Prefilter,
           today: date) -> TriageResult:
    """Code-only priority for the light tier. No network, no LLM."""
    w = universe["triage"]["weights"]
    edu = universe["education"]
    vconf = universe["validation"]
    score, reasons = 0.0, []

    def add(key: str, why: str) -> None:
        nonlocal score
        score += float(w[key])
        reasons.append(why)

    if any(is_education_nic(r.nic_code, edu["nic_prefixes"]) for r in rows):
        add("nic_education", "education NIC code in CIN")
    if any(name_is_educational(r.name, edu, prefilter) for r in rows):
        add("name_education_keyword", "education keyword in name")
    dpiit = [r for r in rows if r.source == "dpiit"]
    if dpiit:
        add("dpiit_recognised", "DPIIT-recognised startup")
        if any(any(t in (r.sector or "").lower() for t in edu["dpiit_terms"]) for r in dpiit):
            add("dpiit_education_sector", "DPIIT sector is education")
    if any(r.source == "scout" for r in rows):
        add("scout_signal", "found by a discovery scout")
    if any(r.source == "yc" for r in rows):
        add("accelerator_listed", "listed in an accelerator directory (YC)")
    if any(i.startswith("ok:cross_validated") for i in issues):
        add("cross_validated", "DPIIT record cross-validated with MCA")
    if any(r.website for r in rows):
        add("website_known", "website known")
    years = [today.year - r.registered_on.year for r in rows if r.registered_on]
    if years and min(years) <= int(universe["triage"]["recent_years"]):
        add("recent", f"incorporated {min(years)} years ago")

    classes = {status_class(r.status, vconf) for r in rows if r.status}
    if universe["triage"].get("require_active", True) and classes == {"inactive"}:
        return TriageResult(score, reasons + ["registry status inactive"], False)
    if any(i.startswith("invalid:") for i in issues):
        return TriageResult(score, reasons + ["invalid registry record"], False)
    return TriageResult(score, reasons, score >= float(universe["triage"]["min_score_for_light"]))


def merge_key(row: RegistryRow, cin_pattern: str) -> str:
    if row.cin and parse_cin(row.cin, cin_pattern):
        return f"cin:{row.cin}"
    return f"name:{row.normalized_name}|{(row.state or '').strip().lower()}"
