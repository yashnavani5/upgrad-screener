"""LinkedIn X-ray via the search router: parse ONLY the result title/snippet (name, headline, company).
The LinkedIn URL is stored as person_url and is never fetched."""

from __future__ import annotations

import re

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.urls import host_of

ROLE_RE = re.compile(
    r"\b(co[- ]?founder|founder|ceo|cto|coo|cfo|chief|director|head|president|vice|manager|lead|engineer|"
    r"consultant|partner|partnerships|officer|advisor|professor|teacher|educator|mentor|coach|trainer|analyst|"
    r"associate|intern|student|investor|product|marketing|sales|operations|growth)\b",
    re.IGNORECASE,
)
AT_RE = re.compile(r"^(?P<role>.+?)\s+(?:at|@)\s+(?P<company>.+)$", re.IGNORECASE)
EXPERIENCE_RE = re.compile(r"Experience:\s*(?P<company>[^·|\n]+)", re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z.'\- ]{1,60}$")


TRUNCATION_RE = re.compile(r"\s*(?:\.\.\.|…)")
ROLE_PREFIX_RE = re.compile(
    r"^(?P<role>(?:co[- ]?founder|founder)(?:\s*(?:&|and|/)\s*(?:ceo|cto|coo|director))?)\s*[-,:]\s*(?P<company>.+)$",
    re.IGNORECASE,
)
NOT_A_COMPANY_RE = re.compile(
    r"(\b(india|delhi|mumbai|bengaluru|bangalore|gurugram|gurgaon|hyderabad|pune|chennai|kolkata|noida|area|taluk|district)\b"
    r"|^ex\b|^former\b|\.\.\.|…|linkedin)",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip(" .,-|\u2013\u2014")


def _company_ok(company: str | None) -> str | None:
    if not company:
        return None
    company = _clean(re.split(r"[,;]", company)[0])  # "Edify.club, DeviceFlow.ai" -> first company
    company = _clean(re.sub(r"^(building|creator of|(?:co-?)?founder of)\s+", "", company, flags=re.IGNORECASE))
    if not company or len(company.split()) > 6 or NOT_A_COMPANY_RE.search(company):
        return None
    return company


def parse_linkedin_result(title: str, snippet: str = "") -> dict[str, str | None] | None:
    """'Asha Rao - Co-founder - Acme Learning | LinkedIn' -> {name, headline, company}. None if no person name.
    Search engines sometimes merge several profiles into one title ('A - X ...B - Y'): only the text before
    the first '...' is used, and everything after a '|' is treated as headline noise."""
    t = TRUNCATION_RE.split(title or "", maxsplit=1)[0]
    t = re.sub(r"\s*[|\u2013\u2014-]\s*LinkedIn\s*$", "", t, flags=re.IGNORECASE)
    t = t.split(" | ", 1)[0].strip()
    parts = [_clean(p) for p in re.split(r"\s+[-\u2013\u2014]\s+", t) if _clean(p)]
    if not parts or not NAME_RE.match(parts[0]) or not 2 <= len(parts[0].split()) <= 5:
        return None
    name, headline, company = parts[0], None, None
    if len(parts) >= 3:
        headline, company = parts[1], parts[2]
    elif len(parts) == 2:
        m = AT_RE.match(parts[1])
        if m:
            headline, company = _clean(m.group("role")), _clean(m.group("company"))
        elif ROLE_RE.search(parts[1]):
            headline = parts[1]
        else:
            company = parts[1]
    if headline and not company:
        m = AT_RE.match(headline) or ROLE_PREFIX_RE.match(headline)
        if m:
            headline, company = _clean(m.group("role")), _clean(m.group("company"))
    elif headline and company:
        m = AT_RE.match(headline) or ROLE_PREFIX_RE.match(headline)
        if m:  # "Founder at X - Something else": the explicit "at X" wins
            headline, company = _clean(m.group("role")), _clean(m.group("company"))
    company = _company_ok(company)
    if not company:
        m = EXPERIENCE_RE.search(snippet or "")
        if m:
            company = _company_ok(m.group("company"))
    return {"name": name, "headline": headline, "company": company}


class XrayScout(BaseScout):
    name = "xray"
    config_key = "xray"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        out: list[DiscoveryItem] = []
        seen: set[str] = set()
        n = int(self.conf.get("results_per_query", 20))
        for q in self.conf.get("queries", []):
            if len(out) >= limit:
                break
            res = await self.ctx.router.search(q, strategy=self.conf.get("strategy", "cheap"), n=n)
            for r in res.results:
                host = host_of(r.url)
                if not (host == "linkedin.com" or host.endswith(".linkedin.com")) or "/in/" not in r.url or r.url in seen:
                    continue
                parsed = parse_linkedin_result(r.title, r.snippet)
                if not parsed or not parsed["company"]:
                    continue
                if not self.ctx.prefilter.matches(r.title, r.snippet):
                    continue
                seen.add(r.url)
                out.append(DiscoveryItem(
                    scout=self.name, raw_company_name=parsed["company"], raw_person_name=parsed["name"],  # type: ignore[arg-type]
                    person_url=r.url, source_url=r.url, snippet=f"{r.title} | {r.snippet}"[:600],
                    evidence_span=r.title, extra={"headline": parsed["headline"], "via": r.provider, "query": q},
                ))
        return out[:limit]
