"""Raw features from supported claims only (corroborated where SPEC requires). Records n (signal count) per
sub-score. No LLM here."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from screener.models import Claim, Company, CompanyAlias, CompanyFounder, Discovery, Founder
from screener.resolve.normalize import normalize_name, registered_domain
from screener.scouts.prefilter import Prefilter
from screener.verify.rules import parse_date, same_org

T_FIELDS = ("prior_exit", "prior_vc_backed", "top_investor", "scale", "years_in_edu", "operator_pedigree",
            "accelerator_or_award")
INDIA = ("india", "bengaluru", "bangalore", "mumbai", "delhi", "new delhi", "gurugram", "gurgaon", "hyderabad",
         "pune", "chennai", "noida", "kolkata", "ahmedabad", "jaipur", "kochi", "chandigarh", "indore", "lucknow",
         "bhopal", "nagpur", "surat", "coimbatore", "thiruvananthapuram", "trivandrum", "bhubaneswar", "patna",
         "guwahati", "vadodara", "mysuru", "mysore", "mangaluru", "visakhapatnam", "kota", "dehradun", "ranchi",
         "raipur", "kanpur", "navi mumbai", "thane", "faridabad", "ghaziabad", "greater noida", "kozhikode")
_PLACES = "|".join(sorted((re.escape(p) for p in INDIA), key=len, reverse=True))
# Phrases in a verified evidence quote that place the company itself in India (not "expanding to India").
INDIA_PHRASES = re.compile(
    rf"\b(?:{_PLACES})[- ]based\b|\bbased (?:out )?(?:of |in )(?:{_PLACES})\b|\bheadquartered in (?:{_PLACES})\b"
    r"|\bindian (?:ed-?tech|startup|start-up|company|platform)\b", re.IGNORECASE)
GEO_FIELDS = ("country", "city")


def usable(c: Claim) -> bool:
    """Supported, and corroborated if the field needs it (exits, funding, revenue)."""
    corr = (c.meta or {}).get("corroboration") or {}
    return c.verified_status == "supported" and (not corr.get("needed") or corr.get("corroborated", False))


@dataclass
class FounderData:
    founder: Founder
    claims: list[Claim]
    raw: dict[str, float | None] = field(default_factory=dict)  # T features before pool percentiles
    signals: int = 0


@dataclass
class CompanyData:
    company: Company
    claims: list[Claim]                       # usable company claims
    all_claims: list[Claim]                   # every company + founder claim (any status)
    founders: list[FounderData]
    aliases: list[str]
    text: str                                  # for embeddings
    momentum_raw: dict[str, tuple[float, float | None]] = field(default_factory=dict)  # feature -> (value, age_days)
    segments: set[str] = field(default_factory=set)
    capabilities: set[str] = field(default_factory=set)
    geo: str = "unknown"
    geo_basis: str = "no location evidence"
    raised_usd: float | None = None           # see size_inputs
    raised_basis: str | None = None
    size_known: bool = False
    founded_year: int | None = None
    stages: list[str] = field(default_factory=list)
    round_dates: list[date] = field(default_factory=list)
    n_fit: int = 0
    verified_claims: int = 0

    @property
    def key(self) -> Any:
        return self.company.id


def _days_since(d: date | None, today: date) -> float | None:
    return float((today - d).days) if d else None


def _matches_any(name: str | None, names: list[str]) -> bool:
    return bool(name) and any(same_org(name, n) for n in names)


def load(session: Session, cfg: Any, companies: list[Company] | None = None, today: date | None = None) -> list[CompanyData]:
    today = today or datetime.now(timezone.utc).date()
    tr = cfg.weights["track_record"]
    wm = cfg.weights["interesting"]["momentum"]
    prefilter = Prefilter(cfg.sources.get("prefilter_keywords", []))
    registry_domains = list(cfg.enrich.get("registry_domains") or [])
    companies = companies if companies is not None else list(session.scalars(select(Company)))
    ids = [c.id for c in companies]
    all_names = {c.id: [c.name] for c in companies}
    for cid, alias in session.execute(select(CompanyAlias.company_id, CompanyAlias.alias)):
        all_names.setdefault(cid, []).append(alias)
    links = session.execute(select(CompanyFounder.company_id, CompanyFounder.founder_id)
                            .where(CompanyFounder.company_id.in_(ids))).all()
    founders = {f.id: f for f in session.scalars(select(Founder).where(Founder.id.in_([f for _, f in links])))}
    claims_by_entity: dict[Any, list[Claim]] = defaultdict(list)
    for c in session.scalars(select(Claim).where(Claim.entity_id.in_(ids + list(founders)))):
        claims_by_entity[c.entity_id].append(c)
    snippets: dict[Any, list[str]] = defaultdict(list)
    for d in session.scalars(select(Discovery).where(Discovery.company_id.in_(ids))):
        if d.snippet:
            snippets[d.company_id].append(d.snippet)

    # Pool-level lookups for founder features.
    funded = {cid for cid in ids if any(usable(c) and c.field == "funding_round" for c in claims_by_entity[cid])}
    funded_names = [n for cid in funded for n in all_names[cid]]
    edu_names = [n for cid in ids for n in all_names[cid]] + list(tr["scaled_edtechs"])
    role_kw = [k.lower() for k in tr["operator_min_role_keywords"]]
    top_inv = list(tr["top_investors"])

    out: list[CompanyData] = []
    for company in companies:
        cclaims = [c for c in claims_by_entity[company.id] if usable(c)]
        fdatas = [FounderData(founders[fid], claims_by_entity[fid]) for cid, fid in links if cid == company.id and fid in founders]
        desc = next((c.value_json.get("text") for c in cclaims if c.field == "description"), None) or company.description
        cd = CompanyData(company, cclaims, list(claims_by_entity[company.id]) + [c for f in fdatas for c in f.claims],
                         fdatas, all_names[company.id], (desc or " ".join(snippets[company.id][:3]))[:2000])

        # --- company-level T inputs
        rounds = [c for c in cclaims if c.field == "funding_round"]
        top_investor = any(_matches_any(inv, top_inv) for c in rounds for inv in (c.value_json.get("investors") or []))
        revenues = [c.value_json.get("amount_usd") for c in cclaims if c.field == "revenue" and c.value_json.get("amount_usd")]
        scale = max(revenues) if revenues else None
        stars = [int(c.value_json.get("stars") or 0) for c in cclaims if c.field == "github_stars"]
        accel = bool(stars) and max(stars) >= int(tr["accelerator_github_min_stars"])
        founded = next((c.value_json.get("year") for c in cclaims if c.field == "founded_year"), None)
        company_signals = len(rounds if top_investor else []) + len(revenues) + (1 if accel else 0)

        for fd in cd.founders:
            fc = [c for c in fd.claims if usable(c)]
            exits = [c for c in fc if c.field == "prior_exit"]
            roles = [c for c in fc if c.field == "prior_role"]
            vc_backed = [c for c in roles if "founder" in (c.value_json.get("role") or "").lower()
                         and (_matches_any(c.value_json.get("company"), funded_names)
                              or _matches_any(c.value_json.get("company"), list(tr["scaled_edtechs"])))]
            pedigree = [c for c in roles if _matches_any(c.value_json.get("company"), list(tr["scaled_edtechs"]))
                        and any(re.search(rf"\b{re.escape(k)}\b", (c.value_json.get("role") or "").lower()) for k in role_kw)]
            edu_years = 0.0
            for c in roles:
                v = c.value_json
                company_name = v.get("company") or ""
                if _matches_any(company_name, edu_names) or prefilter.matches(company_name, v.get("role")):
                    start, end = v.get("start_year"), v.get("end_year") or today.year
                    if start and end and end >= start:
                        edu_years += end - start
            if founded:
                edu_years += max(0, today.year - int(founded))
            fd.raw = {
                "prior_exit": 1.0 if exits else 0.0,
                "prior_vc_backed": 1.0 if vc_backed else 0.0,
                "top_investor": 1.0 if top_investor else 0.0,
                "scale": math.log1p(scale) if scale else None,
                "years_in_edu": min(40.0, edu_years) if edu_years else None,
                "operator_pedigree": 1.0 if pedigree else 0.0,
                "accelerator_or_award": 1.0 if accel else 0.0,
            }
            fd.signals = len(exits) + len(vc_backed) + len(pedigree) + sum(1 for c in roles if c.value_json.get("start_year")) + company_signals

        # --- momentum (value, age in days)
        window = int(wm.get("funding_window_days", 730))
        dated = [(c, parse_date(c.value_json.get("date"))) for c in rounds]
        recent = [d for _, d in dated if d and (today - d).days <= window]
        if dated:
            latest = max((d for _, d in dated if d), default=None)
            cd.momentum_raw["funding_velocity"] = (float(len(recent)), _days_since(latest, today))
        roles_open = [c for c in cclaims if c.field == "open_roles"]
        if roles_open:
            c = max(roles_open, key=lambda x: x.retrieved_at)
            cd.momentum_raw["open_roles"] = (float(c.value_json.get("count") or 0), _days_since(c.retrieved_at.date(), today))
        news = [parse_date(c.value_json.get("date")) for c in cclaims if c.field == "latest_news_date"]
        news = [d for d in news if d]
        if news:
            latest = max(news)
            cd.momentum_raw["news_recency"] = (-float((today - latest).days), _days_since(latest, today))
        if stars:
            push = [parse_date(c.value_json.get("date")) for c in cclaims if c.field == "github_last_push"]
            cd.momentum_raw["github_stars"] = (math.log1p(max(stars)), _days_since(max((d for d in push if d), default=None), today))

        # --- fit inputs
        cd.segments = {c.value_json.get("tag") for c in cclaims if c.field == "segment"} - {None}
        cd.capabilities = {c.value_json.get("tag") for c in cclaims if c.field == "capability"} - {None}
        cd.geo, cd.geo_basis = infer_geo(cclaims, company.domain, registry_domains)
        cd.raised_usd, cd.raised_basis, cd.size_known = size_inputs(cd)
        cd.founded_year = int(founded) if founded else None
        cd.stages = [c.value_json.get("stage") for c in rounds if c.value_json.get("stage")]
        cd.round_dates = [d for _, d in dated if d]
        cd.n_fit = len(cd.segments) + len(cd.capabilities) + (1 if cd.geo != "unknown" else 0)

        # --- confidence: verified claims (company + up to N founders)
        ec = cfg.weights["confidence"]["expected_claims"]
        top_founders = sorted(cd.founders, key=lambda f: -sum(1 for c in f.claims if usable(c)))[: int(ec["max_founders"])]
        cd.verified_claims = len(cclaims) + sum(sum(1 for c in f.claims if usable(c)) for f in top_founders)
        out.append(cd)
    return out


def infer_geo(claims: list[Any], domain: str | None, registry_domains: list[str]) -> tuple[str, str]:
    """(india | outside | unknown, basis) from supported claims, strongest evidence first. Pure."""
    places = " ".join(str((c.value_json or {}).get(k) or "") for c in claims if c.field in GEO_FIELDS
                      for k in GEO_FIELDS).lower()
    if places.strip():
        india = any(re.search(rf"\b{re.escape(w)}\b", places) for w in INDIA)
        return ("india", "country/city claim") if india else ("outside", "country/city claim")
    if any(c.field in ("legal_name", "incorporation_date", "director")
           and (registered_domain(c.source_url) or "") in registry_domains for c in claims):
        return "india", "Indian company registry listing"
    for c in claims:
        if INDIA_PHRASES.search(c.evidence_span or ""):
            return "india", f"evidence quote: {INDIA_PHRASES.search(c.evidence_span).group(0)}"
    if any(c.field in ("funding_round", "revenue", "total_funding") and (
            (c.value_json or {}).get("currency_original") == "INR"
            or str((c.value_json or {}).get("unit_original") or "").lower() in ("crore", "lakh")) for c in claims):
        return "india", "amount stated in INR"
    if domain and domain.endswith((".in", ".co.in")):
        return "india", "website on a .in domain"
    return "unknown", "no location evidence"


def size_inputs(cd: CompanyData) -> tuple[float | None, str | None, bool]:
    """(raised_usd, basis, size_known): the LARGEST of corroborated rounds (sum), a supported total-funding
    statement, and the largest single supported round. Used for the size gate and the acquire lens; exclusion
    evidence may be single-source because wrongly keeping a giant costs more than wrongly dropping one."""
    def supported(field_: str) -> list[float]:
        return [float(v) for c in cd.all_claims if c.verified_status == "supported" and c.field == field_
                and c.entity_type == "company" and (v := (c.value_json or {}).get("amount_usd"))]

    options = [(float(cd.company.total_raised_usd or 0), "corroborated funding rounds"),
               (max(supported("total_funding"), default=0.0), "total funding statement (single source)"),
               (max(supported("funding_round"), default=0.0), "largest funding round (single source)")]
    value, basis = max(options, key=lambda x: x[0])
    if value > 0:
        return value, basis, True
    return None, None, headcount_value(cd) is not None


def headcount_value(cd: CompanyData) -> int | None:
    for c in cd.claims:
        if c.field == "headcount":
            m = re.search(r"\d[\d,]*", str(c.value_json.get("text") or ""))
            if m:
                return int(m.group(0).replace(",", ""))
    return None


def normalized_names(cd: CompanyData) -> set[str]:
    return {normalize_name(n) for n in cd.aliases if n}
