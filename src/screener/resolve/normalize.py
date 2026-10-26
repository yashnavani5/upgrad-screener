"""Name and domain normalization for entity resolution."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from urllib.parse import urlsplit

import tldextract
from unidecode import unidecode

# Offline: bundled public-suffix snapshot (no network fetch). Private suffixes on, so x.github.io and
# y.github.io (or *.vercel.app) are different domains.
_extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)

DOMAIN_LIKE = re.compile(r"^[a-z0-9][a-z0-9\-]*(\.[a-z0-9\-]+)+$", re.IGNORECASE)
HONORIFICS = ("dr", "mr", "mrs", "ms", "prof", "er", "ca", "adv")


def _basic(text: str) -> str:
    text = unidecode(text or "").lower().replace("&", " and ").replace("'", "")  # "Byju's" -> "byjus"
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=16)
def _suffix_patterns(suffixes: tuple[str, ...]) -> list[re.Pattern[str]]:
    # Longest first so "private limited" is removed before "limited".
    ordered = sorted({_basic(s) for s in suffixes if _basic(s)}, key=len, reverse=True)
    return [re.compile(r"(?:^|\s)" + re.escape(s) + r"$") for s in ordered]


def registered_domain(url_or_host: str | None) -> str | None:
    """eTLD+1 ('https://www.xyz.co.in/a' -> 'xyz.co.in'); None if there is no registrable domain."""
    if not url_or_host:
        return None
    host = urlsplit(url_or_host if "://" in url_or_host else f"https://{url_or_host}").hostname or ""
    ext = _extract(host.lower())
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


def domain_label(domain: str | None) -> str | None:
    """'xyz.co.in' -> 'xyz'."""
    if not domain:
        return None
    return _extract(domain).domain or None


def looks_like_domain(name: str) -> bool:
    return bool(DOMAIN_LIKE.match((name or "").strip())) and registered_domain(name.strip()) is not None


def normalize_name(name: str, suffixes: Iterable[str] = ()) -> str:
    """Lowercase, transliterate, drop punctuation and trailing legal/descriptor suffixes (never to empty).
    A domain-like name ('xyz.in') normalizes to its registrable label ('xyz')."""
    raw = (name or "").strip()
    if looks_like_domain(raw):
        return _basic(domain_label(registered_domain(raw)) or raw)
    text = _basic(raw)
    patterns = _suffix_patterns(tuple(suffixes))
    changed = True
    while changed:
        changed = False
        for pat in patterns:
            stripped = pat.sub("", text).strip()
            if stripped and stripped != text:
                text, changed = stripped, True
                break
    return text


def is_listed_domain(domain: str | None, listed: Iterable[str]) -> bool:
    if not domain:
        return False
    return any(domain == d or domain.endswith("." + d) for d in listed)


def company_domain(url: str | None, shared_domains: Iterable[str]) -> str | None:
    """Registrable domain of a company URL, or None for shared platforms (github.com, linkedin.com, ...)."""
    domain = registered_domain(url)
    return None if domain is None or is_listed_domain(domain, shared_domains) else domain


def normalize_person(name: str) -> str:
    tokens = _basic(name).split()
    while tokens and tokens[0] in HONORIFICS:
        tokens = tokens[1:]
    return " ".join(tokens)


def same_person(a: str, b: str) -> bool:
    """Same person within ONE company: the same name tokens in any order ("Minh Thu Do" / "Do Minh Thu"), or a
    name of 2+ tokens contained in the other ("Lam Do" / "Do Ngoc Lam"). Only for founders of the same company:
    across companies a shared name is not evidence of the same person."""
    ta, tb = normalize_person(a).split(), normalize_person(b).split()
    if len(ta) < 2 or len(tb) < 2:
        return False
    sa, sb = set(ta), set(tb)
    if sa == sb or (min(len(sa), len(sb)) >= 2 and (sa <= sb or sb <= sa)):
        return True
    # Abbreviated surname: "Sameer S." / "Sameer Sadana" (same first name, last token is the other's initial).
    short, long_ = (ta, tb) if len(ta[-1]) <= len(tb[-1]) else (tb, ta)
    return short[0] == long_[0] and len(short[-1]) == 1 and long_[-1].startswith(short[-1]) and len(long_[-1]) > 1


def surname(name: str) -> str | None:
    tokens = normalize_person(name).split()
    return tokens[-1] if len(tokens) >= 2 else None


def looks_like_person(name: str | None) -> bool:
    """Real-name heuristic: at least two alphabetic tokens (GitHub handles like 'hneemann' fail)."""
    tokens = normalize_person(name or "").split()
    return len(tokens) >= 2 and all(t.isalpha() for t in tokens)


def person_hard_key(url: str | None) -> str | None:
    """Stable identity URL key for LinkedIn / GitHub / X profiles, else None."""
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    host = (parts.hostname or "").lower()
    path = [p for p in parts.path.split("/") if p]
    if (host == "linkedin.com" or host.endswith(".linkedin.com")) and len(path) >= 2 and path[0] == "in":
        return f"linkedin.com/in/{path[1].lower()}"
    if host in ("github.com", "www.github.com") and len(path) >= 1:
        return f"github.com/{path[0].lower()}"
    if host in ("x.com", "twitter.com", "www.x.com", "www.twitter.com") and len(path) >= 1:
        return f"x.com/{path[0].lower()}"
    return None
