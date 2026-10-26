"""Fetcher behaviour without real network: policy block, robots.txt decisions, extraction."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from screener.cache import DiskCache
from screener.config import AppConfig
from screener.http import Fetcher, looks_js_rendered
from screener.ratelimit import RateLimiter

ARTICLE = "<html><body><article><h1>Title</h1>" + "<p>Education founders in India are building. </p>" * 40 + "</article></body></html>"


def make_fetcher(cfg: AppConfig, tmp_path: Path) -> Fetcher:
    client = httpx.AsyncClient(follow_redirects=True, event_hooks={"request": [Fetcher._block_policy_hosts]})
    return Fetcher(
        cfg,
        limiter=RateLimiter(default_rpm=6000, max_concurrency=4),
        fetch_cache=DiskCache(tmp_path, "fetch", None),
        robots_cache=DiskCache(tmp_path, "robots", None),
        client=client,
    )


@respx.mock(assert_all_called=False)
async def test_linkedin_is_blocked_without_any_request(cfg: AppConfig, tmp_path: Path) -> None:
    route = respx.route(host__regex=r".*linkedin\.com")
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://www.linkedin.com/in/someone")
    assert result.outcome == "blocked_by_policy"
    assert not route.called


@respx.mock
async def test_redirect_to_linkedin_is_blocked(cfg: AppConfig, tmp_path: Path) -> None:
    respx.get("https://short.example/robots.txt").respond(404)
    respx.get("https://short.example/x").respond(302, headers={"Location": "https://www.linkedin.com/in/x"})
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://short.example/x", allow_fallbacks=False)
    assert result.outcome == "blocked_by_policy"


@respx.mock
async def test_robots_disallow_prevents_fetch(cfg: AppConfig, tmp_path: Path) -> None:
    respx.get("https://site.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /private\n")
    page = respx.get("https://site.example/private/page")
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://site.example/private/page")
    assert result.outcome == "blocked_by_robots"
    assert result.robots is not None and not result.robots.allowed
    assert not page.called


@respx.mock
async def test_robots_404_allows_and_text_is_extracted(cfg: AppConfig, tmp_path: Path) -> None:
    respx.get("https://site.example/robots.txt").respond(404)
    respx.get("https://site.example/a").respond(200, html=ARTICLE)
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://site.example/a", allow_fallbacks=False)
        again = await f.fetch("https://site.example/a", allow_fallbacks=False)
    assert result.outcome == "ok" and result.method == "httpx"
    assert "Education founders in India" in (result.text or "")
    assert result.robots is not None and result.robots.allowed
    assert again.from_cache


@respx.mock
async def test_robots_5xx_is_treated_as_disallow(cfg: AppConfig, tmp_path: Path) -> None:
    respx.get("https://down.example/robots.txt").respond(503)
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://down.example/a")
    assert result.outcome == "blocked_by_robots"
    assert "RFC 9309" in (result.error or "")


@respx.mock
async def test_non_html_is_reported(cfg: AppConfig, tmp_path: Path) -> None:
    respx.get("https://site.example/robots.txt").respond(404)
    respx.get("https://site.example/f.pdf").respond(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    async with make_fetcher(cfg, tmp_path) as f:
        result = await f.fetch("https://site.example/f.pdf", allow_fallbacks=False)
    assert result.outcome == "not_html"


def test_js_shell_detection() -> None:
    assert looks_js_rendered('<div id="root"></div>', "", 400)
    assert not looks_js_rendered(ARTICLE, "x" * 2000, 400)


@pytest.mark.parametrize("text_len", [0, 399])
def test_short_text_triggers_fallback(text_len: int) -> None:
    assert looks_js_rendered("<html></html>", "x" * text_len, 400)


@respx.mock
async def test_robots_network_error_is_not_cached(cfg: AppConfig, tmp_path: Path) -> None:
    route = respx.get("https://flaky.example/robots.txt")
    route.side_effect = [httpx.ConnectTimeout("timeout"), httpx.ConnectTimeout("timeout"), httpx.Response(404)]
    async with make_fetcher(cfg, tmp_path) as f:
        first = await f.robots_decision("https://flaky.example/a")
        second = await f.robots_decision("https://flaky.example/a")
    assert not first.allowed and "RFC 9309" in first.reason   # unreachable -> disallow for now
    assert second.allowed                                      # retried later, not stuck on the cached failure
