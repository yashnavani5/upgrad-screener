"""URL normalization, dedupe keys and the LinkedIn/never-fetch policy."""

from __future__ import annotations

import base64
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Hosts we never fetch (CLAUDE.md rule 4; Crunchbase/Tracxn are search-snippet-only by policy). Subdomains included.
POLICY_BLOCKED_HOSTS = ("linkedin.com", "lnkd.in", "crunchbase.com", "tracxn.com", "x.com", "twitter.com")

TRACKING_PARAMS = frozenset(
    {
        "gclid", "dclid", "fbclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid", "_hsenc", "_hsmi",
        "ref", "ref_src", "ref_url", "spm", "si", "srsltid", "trk", "trkinfo", "s_cid", "cmpid", "ito",
    }
)
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_")
DEFAULT_PORTS = {"http": 80, "https": 443}


def host_of(url: str) -> str:
    return (urlsplit(url if "://" in url else f"https://{url}").hostname or "").lower()


def is_blocked_by_policy(url: str) -> bool:
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in POLICY_BLOCKED_HOSTS)


def _bing_target(u: str) -> str | None:
    # Bing click-tracking: u=a1<base64url of target>
    if not u.startswith("a1"):
        return None
    payload = u[2:]
    try:
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def unwrap_redirect(url: str) -> str:
    """Unwrap search-engine redirect links (DuckDuckGo, Google, Bing) to the target URL."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    target: str | None = None
    if host.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = params.get("uddg")
    elif (host.startswith("google.") or ".google." in host) and parts.path == "/url":
        target = params.get("q") or params.get("url")
    elif host.endswith("bing.com") and parts.path == "/ck/a":
        target = _bing_target(params.get("u", ""))
    if target and target.startswith(("http://", "https://")):
        return unwrap_redirect(unquote(target)) if "%3A%2F%2F" in target else target
    return url


def ascii_url(url: str) -> str:
    """Request-safe URL: IDNA host and percent-encoded path/query ('/funding-₹-500-crore' cannot be sent as is).
    Already-encoded parts are kept; the result points to the same resource."""
    if url.isascii():
        return url
    p = urlsplit(url)
    host = p.hostname or ""
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except UnicodeError:
        ascii_host = quote(host)
    netloc = p.netloc.replace(host, ascii_host) if host else p.netloc
    path = quote(p.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(p.query, safe="=&%:@!$'()*+,;/?-._~")
    return urlunsplit((p.scheme, netloc, path, query, quote(p.fragment, safe="%-._~")))


def normalize_url(url: str) -> str:
    """Canonical form for storage: unwrap redirects, lowercase scheme/host, drop default port, fragment,
    tracking params and trailing slash; sort the query. Keeps `www.` and the scheme so the URL still works."""
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url.lstrip("/")
    url = unwrap_redirect(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    netloc = host
    if parts.port and DEFAULT_PORTS.get(scheme) != parts.port:
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def dedupe_key(url: str) -> str:
    """Identity for dedupe: normalized URL without scheme and without a leading `www.`."""
    parts = urlsplit(normalize_url(url))
    host = parts.netloc[4:] if parts.netloc.startswith("www.") else parts.netloc
    path = "" if parts.path == "/" else parts.path
    return host + path + (f"?{parts.query}" if parts.query else "")
