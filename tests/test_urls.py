from __future__ import annotations

import base64

import pytest

from screener.search import SearchResult, dedupe
from screener.urls import dedupe_key, is_blocked_by_policy, normalize_url, unwrap_redirect


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Path/", "https://example.com/Path"),
        ("https://example.com", "https://example.com/"),
        ("example.com/a", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:8080/a", "http://example.com:8080/a"),
        ("https://example.com/a#section", "https://example.com/a"),
        ("https://example.com/a?utm_source=x&b=2&a=1&fbclid=z", "https://example.com/a?a=1&b=2"),
        ("https://www.example.com/a/", "https://www.example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
    ],
)
def test_normalize_url(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


def test_unwrap_duckduckgo_redirect() -> None:
    url = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Finc42.com%2Fbuzz%2Fx%2F&rut=abc"
    assert normalize_url(url) == "https://inc42.com/buzz/x"


def test_unwrap_google_redirect() -> None:
    assert unwrap_redirect("https://www.google.com/url?q=https://yourstory.com/a&sa=U") == "https://yourstory.com/a"


def test_unwrap_bing_redirect() -> None:
    target = "https://entrackr.com/2026/10/x"
    encoded = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    assert unwrap_redirect(f"https://www.bing.com/ck/a?!&&p=1&u=a1{encoded}&ntb=1") == target


def test_dedupe_key_ignores_scheme_www_trailing_slash_tracking() -> None:
    keys = {
        dedupe_key("https://www.example.com/a/"),
        dedupe_key("http://example.com/a"),
        dedupe_key("https://example.com/a?utm_medium=email"),
        dedupe_key("https://EXAMPLE.com/a#top"),
    }
    assert keys == {"example.com/a"}


def test_dedupe_key_keeps_meaningful_query_and_path_case() -> None:
    assert dedupe_key("https://example.com/a?id=1") != dedupe_key("https://example.com/a?id=2")
    assert dedupe_key("https://example.com/A") != dedupe_key("https://example.com/a")


def test_dedupe_results_keeps_first() -> None:
    rows = [
        SearchResult(title="1", url="https://www.a.com/x/", provider="searxng", rank=1),
        SearchResult(title="2", url="https://a.com/x?utm_source=y", provider="ddgs", rank=1),
        SearchResult(title="3", url="https://b.com/", provider="ddgs", rank=2),
    ]
    out = dedupe(rows)
    assert [r.title for r in out] == ["1", "3"]


@pytest.mark.parametrize(
    ("url", "blocked"),
    [
        ("https://www.linkedin.com/in/someone", True),
        ("https://in.linkedin.com/company/x", True),
        ("linkedin.com/in/x", True),
        ("https://lnkd.in/abc", True),
        ("https://notlinkedin.com/x", False),
        ("https://inc42.com/", False),
    ],
)
def test_policy_blocklist(url: str, blocked: bool) -> None:
    assert is_blocked_by_policy(url) is blocked


def test_ascii_url_percent_encodes_non_ascii_paths() -> None:
    from screener.urls import ascii_url

    assert ascii_url("https://example.com/a") == "https://example.com/a"
    assert ascii_url("https://inc42.com/funding/raises-₹500-crore") == "https://inc42.com/funding/raises-%E2%82%B9500-crore"
    assert ascii_url("https://example.com/a%20b?q=₹") == "https://example.com/a%20b?q=%E2%82%B9"  # existing escapes kept
    assert ascii_url("https://bücher.de/x").startswith("https://xn--bcher-kva.de/")
