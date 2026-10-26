"""X (Twitter) via search-engine results only. x.com is never fetched: its robots.txt disallows automated access
(including publish.x.com/oembed), and the API is paid. Profile results look like
    title:   'Asha Rao (@asharao) / X'
    snippet: 'Co-founder & CEO @AcmeLearning | ex-Byju's | IIT Delhi'
and the founder/company is parsed from the bio. The x.com profile URL is stored as person_url (identity key).
Like LinkedIn snippets, an X bio never verifies identity on its own (verify/rules.py)."""

from __future__ import annotations

import re

from screener.scouts.base import BaseScout, DiscoveryItem
from screener.urls import host_of

X_HOSTS = ("x.com", "twitter.com")
PROFILE_TITLE_RE = re.compile(r"^(?P<name>[^()@|]{2,60}?)\s*\(@(?P<handle>[A-Za-z0-9_]{1,15})\)", re.IGNORECASE)
# "Co-founder & CEO @AcmeLearning", "Founder, Acme Learning", "Building @acme", "founder of Acme Learning"
BIO_COMPANY_RE = re.compile(
    r"(?:co[- ]?founder|founder|building|ceo)(?:\s*(?:&|and|/)\s*(?:ceo|cto|coo))?\s*(?:of|at|@|,|-|:)\s*@?"
    r"(?P<company>[A-Za-z0-9][A-Za-z0-9.&' ]{1,40}?)(?=\s*(?:[|,;.·•]|\s(?:ex|prev|former)\b|$))",
    re.IGNORECASE,
)
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z.'\- ]{1,60}$")
GENERIC_WORDS = {"the", "a", "an", "stealth", "my", "our", "multiple", "two", "three", "edtech", "startups", "startup"}


def is_x(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in X_HOSTS)


def profile_handle(url: str) -> str | None:
    """'https://x.com/asharao' -> 'asharao'; post, media and search URLs -> None."""
    if not is_x(url):
        return None
    path = [p for p in re.sub(r"^https?://[^/]+", "", url).split("?")[0].split("/") if p]
    if len(path) != 1 or path[0].lower() in ("search", "home", "explore", "i", "hashtag", "intent", "share"):
        return None
    return path[0]


def parse_x_profile(title: str, snippet: str = "") -> dict[str, str | None] | None:
    """{name, handle, company, bio} from an X profile search result; None if it is not a person's profile."""
    m = PROFILE_TITLE_RE.match((title or "").strip())
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip(" .-|")
    if not NAME_RE.match(name) or not 2 <= len(name.split()) <= 5:
        return None
    bio = re.sub(r"\s+", " ", snippet or "").strip()
    c = BIO_COMPANY_RE.search(bio)
    company = c.group("company").strip(" .-'") if c else None
    # A real company name: an @handle or a capitalised name, not a description ("an edtech platform").
    is_handle = bool(c) and bio[c.start("company") - 1:c.start("company")] == "@"
    if company and (len(company) < 2 or company.split()[0].lower() in GENERIC_WORDS
                    or not (is_handle or company[0].isupper() or company[0].isdigit())):
        company = None
    return {"name": name, "handle": m.group("handle"), "company": company, "bio": bio or None}


class XScout(BaseScout):
    name = "x"
    config_key = "x"

    async def discover(self, limit: int) -> list[DiscoveryItem]:
        out: list[DiscoveryItem] = []
        seen: set[str] = set()
        n = int(self.conf.get("results_per_query", 20))
        for q in self.conf.get("queries", []):
            if len(out) >= limit:
                break
            res = await self.ctx.router.search(q, strategy=self.conf.get("strategy", "cheap"), n=n)
            for r in res.results:
                handle = profile_handle(r.url)
                if not handle or handle.lower() in seen:
                    continue
                parsed = parse_x_profile(r.title, r.snippet)
                if not parsed or not parsed["company"] or not self.ctx.prefilter.matches(r.title, r.snippet):
                    continue
                seen.add(handle.lower())
                out.append(DiscoveryItem(
                    scout=self.name, raw_company_name=parsed["company"], raw_person_name=parsed["name"],  # type: ignore[arg-type]
                    person_url=f"https://x.com/{handle}", source_url=f"https://x.com/{handle}",
                    snippet=f"{r.title} | {r.snippet}"[:600], evidence_span=r.title,
                    extra={"headline": parsed["bio"], "via": r.provider, "query": q, "x_handle": handle},
                ))
        return out[:limit]
