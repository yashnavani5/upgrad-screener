"""Gemini USD spend caps: reserve before sending, settle to actual cost, refuse (never send) over a cap."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from screener.budget import SPEND_KEY, from_config, this_month, today
from screener.config import Settings, load_config
from screener.db import MemoryQuotaStore
from screener.llm import GeminiClient, LLMBudgetExceeded, LLMConfigError


def _client(tmp_path: Path, *, run_usd: float | None = None, daily: float | None = None,
            monthly: float | None = None, prices: dict | None = None) -> GeminiClient:
    settings = Settings(_env_file=None, cache_dir=tmp_path / "cache", gemini_api_key="test-key",
                        gemini_model_fast="model-fast", run_budget_usd=run_usd)
    cfg = load_config(settings)
    cfg.providers["llm"]["prices_per_million_tokens"] = prices or {
        "fast": {"input": 1.0, "output": 10.0}, "smart": {"input": 1.0, "output": 10.0},
    }
    cfg.providers["llm"]["reserve_output_tokens"] = 1000
    cfg.providers["llm"]["grounding_usd_per_request"] = 0.5
    cfg.providers["budget"]["gemini_daily_usd_limit"] = daily
    cfg.providers["budget"]["gemini_monthly_usd_limit"] = monthly
    gemini = GeminiClient(cfg)
    gemini.usage_store = MemoryQuotaStore()
    return gemini


def _response(tin: int, tout: int, thoughts: int = 0, tool: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=tin, candidates_token_count=tout, thoughts_token_count=thoughts,
        tool_use_prompt_token_count=tool))


def _call(gemini: GeminiClient, response: object, *, endpoint: str = "generate_json", tokens_in: int = 1000) -> list[int]:
    sent: list[int] = []

    async def fn() -> object:
        sent.append(1)
        return response

    asyncio.run(gemini.call("fast", endpoint, fn, est_tokens_in=tokens_in))
    return sent


def test_cost_counts_thinking_tokens(tmp_path: Path) -> None:
    gemini = _client(tmp_path)
    _call(gemini, _response(1000, 100, thoughts=900))
    # 1000 in * $1/M + (100 + 900) out * $10/M = 0.001 + 0.01
    assert gemini.spent_usd == pytest.approx(0.011)
    assert gemini.usage_store.used(SPEND_KEY, today()) == pytest.approx(0.011)
    assert gemini.usage_store.used(SPEND_KEY, this_month()) == pytest.approx(0.011)


def test_grounded_call_adds_fee_and_tool_tokens(tmp_path: Path) -> None:
    gemini = _client(tmp_path)
    _call(gemini, _response(1000, 0, tool=1000), endpoint="grounded")
    assert gemini.spent_usd == pytest.approx(0.002 + 0.5)


def test_daily_cap_refuses_before_sending(tmp_path: Path) -> None:
    gemini = _client(tmp_path, daily=0.015)
    _call(gemini, _response(1000, 1000))            # estimate 0.011 fits, actual 0.011
    with pytest.raises(LLMBudgetExceeded) as exc:
        _call(gemini, _response(1000, 1000))        # 0.011 + 0.011 > 0.015: refused
    assert exc.value.scope == "day"
    assert gemini.calls == 1
    assert gemini.last_block is not None
    assert gemini.usage_store.used(SPEND_KEY, today()) == pytest.approx(0.011)


def test_monthly_cap_and_rollback_of_day_reservation(tmp_path: Path) -> None:
    gemini = _client(tmp_path, daily=10.0, monthly=0.005)
    with pytest.raises(LLMBudgetExceeded) as exc:
        _call(gemini, _response(10, 10))
    assert exc.value.scope == "month"
    assert gemini.usage_store.used(SPEND_KEY, today()) == 0.0  # the day reservation was released


def test_run_cap_is_per_process(tmp_path: Path) -> None:
    shared = MemoryQuotaStore()
    a = _client(tmp_path, run_usd=0.015)
    a.usage_store = shared
    _call(a, _response(1000, 1000))
    with pytest.raises(LLMBudgetExceeded) as exc:
        _call(a, _response(1000, 1000))
    assert exc.value.scope == "run"
    b = _client(tmp_path, run_usd=0.015)
    b.usage_store = shared
    assert _call(b, _response(1000, 1000)) == [1]   # another process has its own run budget


def test_caps_are_shared_across_clients(tmp_path: Path) -> None:
    shared = MemoryQuotaStore()
    a, b = _client(tmp_path, daily=0.015), _client(tmp_path, daily=0.015)
    a.usage_store = b.usage_store = shared
    _call(a, _response(1000, 1000))
    with pytest.raises(LLMBudgetExceeded):
        _call(b, _response(1000, 1000))


def test_settles_to_actual_and_releases_failed_calls(tmp_path: Path) -> None:
    gemini = _client(tmp_path)
    _call(gemini, _response(10, 10))                # far below the 1000-token output reservation
    assert gemini.spent_usd == pytest.approx(10 / 1e6 + 100 / 1e6)

    async def boom() -> object:
        raise RuntimeError("network")

    before = gemini.usage_store.used(SPEND_KEY, today())
    with pytest.raises(RuntimeError):
        asyncio.run(gemini.call("fast", "generate_json", boom, est_tokens_in=1000))
    # Unknown outcome: the reservation is kept (assume billed) rather than undercounting.
    assert gemini.usage_store.used(SPEND_KEY, today()) > before


def test_null_price_with_cap_fails_closed(tmp_path: Path) -> None:
    gemini = _client(tmp_path, daily=5.0, prices={"fast": {"input": None, "output": None}})
    with pytest.raises(LLMConfigError):
        _call(gemini, _response(10, 10))


def test_null_price_without_caps_still_calls(tmp_path: Path) -> None:
    gemini = _client(tmp_path, prices={"fast": {"input": None, "output": None}})
    assert _call(gemini, _response(10, 10)) == [1]


def test_budget_guard_sizes_from_usd_left(tmp_path: Path) -> None:
    gemini = _client(tmp_path, daily=1.0)
    gemini.cfg.providers["budget"]["estimates"]["usd_per_call"] = 0.01
    guard = from_config(gemini.cfg, gemini, None)
    assert guard.remaining() == 100                 # $1 left / $0.01 per call
    gemini.usage_store.try_consume(SPEND_KEY, today(), 0.95, None)
    assert guard.remaining() == 5
