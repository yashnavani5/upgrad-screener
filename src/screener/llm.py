"""Gemini via the google-genai SDK: structured JSON generation, grounding probe and grounded search.

Model names come only from GEMINI_MODEL_FAST / GEMINI_MODEL_SMART / GEMINI_EMBED_MODEL.
The LLM extracts, verifies and explains; it never produces a final numeric score.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from screener.cache import DiskCache, get_cache
from screener.config import AppConfig, get_config
from screener.ratelimit import RETRYABLE_STATUS, RetryableError, parse_retry_after, retry_async
from screener.telemetry import CallRecord, arecord, timed
from screener.urls import normalize_url

log = logging.getLogger(__name__)

Role = Literal["fast", "smart", "embed"]
M = TypeVar("M", bound=BaseModel)

GROUNDING_PROBE_PROMPT = "Use Google Search. In one sentence: what is upGrad (the Indian education company)?"


class LLMConfigError(RuntimeError):
    """Missing key or model name, or configured model not available to this key."""


class LLMBudgetExceeded(RuntimeError):
    """A Gemini cap was reached; the call was NOT sent. `scope`: 'run' (RUN_MAX_LLM_CALLS / RUN_BUDGET_USD for
    this process), 'day' or 'month' (USD spend caps shared by every process)."""

    def __init__(self, message: str, scope: str = "run") -> None:
        super().__init__(message)
        self.scope = scope


def _add(a: int | None, b: int | None) -> int | None:
    return None if a is None and b is None else (a or 0) + (b or 0)


def _approx_tokens(*texts: str | None) -> int:
    return sum(len(t or "") for t in texts) // 4 + 1


def _retry_after_from_error(exc: genai_errors.APIError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        value = parse_retry_after(headers.get("retry-after"))
        if value is not None:
            return value
    # Gemini puts google.rpc.RetryInfo {"retryDelay": "17s"} in the error details.
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", str(getattr(exc, "details", "") or exc))
    return float(match.group(1)) if match else None


class GeminiClient:
    def __init__(
        self,
        cfg: AppConfig | None = None,
        cache: DiskCache | None = None,
    ) -> None:
        self.cfg = cfg or get_config()
        s = self.cfg.settings
        if s.gemini_api_key is None:
            raise LLMConfigError("GEMINI_API_KEY is not set (see .env.example)")
        self.client = genai.Client(api_key=s.gemini_api_key.get_secret_value())
        self.cache = cache or get_cache("llm")
        llm_cfg = self.cfg.providers.get("llm", {})
        self.prices: dict[str, dict[str, float | None]] = llm_cfg.get("prices_per_million_tokens", {})
        self.max_retries = int(llm_cfg.get("max_retries", 5))
        self.max_calls: float = s.run_max_llm_calls
        self.calls = 0
        # Spend caps (USD). Every call reserves its estimated cost against each cap before it is sent, then
        # settles to the actual cost; a reservation that doesn't fit raises LLMBudgetExceeded instead.
        budget = self.cfg.providers.get("budget", {})
        self.run_budget_usd: float | None = s.run_budget_usd
        self.daily_usd_limit: float | None = budget.get("gemini_daily_usd_limit")
        self.monthly_usd_limit: float | None = budget.get("gemini_monthly_usd_limit")
        self.grounding_usd = float(llm_cfg.get("grounding_usd_per_request") or 0.0)
        self.reserve_output_tokens = int(llm_cfg.get("reserve_output_tokens", 2000))
        self.thinking_level: dict[str, str | None] = llm_cfg.get("thinking_level") or {}
        self.spent_usd = 0.0
        self.last_block: tuple[float, LLMBudgetExceeded] | None = None
        self._grounding: bool | None = None
        # Usage counters (QuotaStore, normally DbQuotaStore so every process shares them): calls per UTC day
        # and USD per UTC day / month. Without one, a process-local store still enforces the caps in-process.
        # Counting and capping only: Gemini is never rate limited by this client.
        self.usage_store: Any = None
        from screener.db import MemoryQuotaStore

        self._local_store = MemoryQuotaStore()
        self.grounding_reason: str | None = None
        self._redirects = httpx.AsyncClient(headers={"User-Agent": s.user_agent}, timeout=10.0, follow_redirects=False)

    async def aclose(self) -> None:
        await self._redirects.aclose()

    # --- models ------------------------------------------------------------------------------------

    def model_name(self, role: Role) -> str:
        s = self.cfg.settings
        name = {"fast": s.gemini_model_fast, "smart": s.gemini_model_smart, "embed": s.gemini_embed_model}[role]
        if not name:
            raise LLMConfigError(f"GEMINI_MODEL_{role.upper()} is not set in .env")
        return name

    async def list_models(self) -> list[types.Model]:
        with timed() as t:
            try:
                models = [m async for m in await self.client.aio.models.list()]
            except genai_errors.APIError as exc:
                await arecord(CallRecord("gemini", "models.list", f"http_{exc.code}", exc.code, t["ms"], error=str(exc)))
                raise
        await arecord(CallRecord("gemini", "models.list", "ok", 200, t["ms"]))
        return models

    async def ensure_models_available(self) -> dict[str, str]:
        """Fail with a clear message if a configured model isn't available to this key."""
        models = await self.list_models()
        available = {(m.name or "").removeprefix("models/"): m for m in models}
        resolved, problems = {}, []
        for role, action in (("fast", "generateContent"), ("smart", "generateContent"), ("embed", "embedContent")):
            try:
                name = self.model_name(role)  # type: ignore[arg-type]
            except LLMConfigError as exc:
                problems.append(str(exc))
                continue
            model = available.get(name.removeprefix("models/"))
            if model is None:
                problems.append(f"GEMINI_MODEL_{role.upper()}={name} is not available to this API key")
            elif model.supported_actions and action not in model.supported_actions:
                problems.append(f"GEMINI_MODEL_{role.upper()}={name} does not support {action}")
            else:
                resolved[role] = name
        if problems:
            gen = sorted(n for n, m in available.items() if "generateContent" in (m.supported_actions or []))
            emb = sorted(n for n, m in available.items() if "embedContent" in (m.supported_actions or []))
            raise LLMConfigError(
                "; ".join(problems)
                + f". Text models available: {', '.join(gen) or 'none'}. Embedding models: {', '.join(emb) or 'none'}."
            )
        return resolved

    # --- core call with limits, retries and accounting --------------------------------------------

    async def call(self, role: Role, endpoint: str, fn: Callable[[], Awaitable[Any]], *, est_tokens_in: int = 0) -> Any:
        if self.calls >= self.max_calls:
            raise self._blocked(LLMBudgetExceeded(f"RUN_MAX_LLM_CALLS={self.max_calls} reached"))
        name = self.model_name(role)
        grounded = endpoint == "grounded"
        estimate = self._estimate(role, est_tokens_in, grounded)
        periods = await asyncio.to_thread(self._reserve, estimate)
        self.calls += 1
        if self.usage_store is not None:
            try:
                from screener.budget import DAILY_KEY, today

                self.usage_store.try_consume(DAILY_KEY, today(), 1, None)
            except Exception:  # counting must never break a call
                pass

        # No client-side rate limiting for Gemini: calls go out immediately. A 429/5xx from the API is
        # still retried (honoring the server's retry delay) so a transient error doesn't fail the run.
        async def attempt() -> Any:
            try:
                return await fn()
            except genai_errors.APIError as exc:
                if exc.code in RETRYABLE_STATUS:
                    raise RetryableError(str(exc), _retry_after_from_error(exc), exc.code) from exc
                raise

        with timed() as t:
            try:
                response = await retry_async(attempt, attempts=self.max_retries, base=2.0, cap=90.0)
            except (RetryableError, genai_errors.APIError) as exc:
                await asyncio.to_thread(self._settle, periods, estimate, 0.0)  # failed requests are not billed
                code = getattr(exc, "status", None) or getattr(exc, "code", None)
                await arecord(CallRecord("gemini", f"{endpoint}:{name}", "error", code, t["ms"], error=str(exc)[:500]))
                raise
            except BaseException:
                await asyncio.to_thread(self._settle, periods, estimate, estimate)  # unknown: assume billed
                raise
        usage = getattr(response, "usage_metadata", None)
        tin = _add(getattr(usage, "prompt_token_count", None), getattr(usage, "tool_use_prompt_token_count", None))
        # Thinking tokens are billed as output but are not part of candidates_token_count.
        tout = _add(getattr(usage, "candidates_token_count", None), getattr(usage, "thoughts_token_count", None))
        cost = self._cost(role, tin, tout, grounded)
        await asyncio.to_thread(self._settle, periods, estimate, estimate if cost is None or tin is None else cost)
        await arecord(CallRecord("gemini", f"{endpoint}:{name}", "ok", 200, t["ms"], tin, tout, cost))
        return response

    def _cost(self, role: str, tokens_in: int | None, tokens_out: int | None, grounded: bool = False) -> float | None:
        price = self.prices.get(role) or {}
        pin, pout = price.get("input"), price.get("output")
        if pin is None or (pout is None and role != "embed"):
            return None
        tokens = (tokens_in or 0) * pin + (tokens_out or 0) * (pout or 0.0)
        return tokens / 1_000_000 + (self.grounding_usd if grounded else 0.0)

    # --- spend caps --------------------------------------------------------------------------------

    def _caps_active(self) -> bool:
        return any(c is not None for c in (self.run_budget_usd, self.daily_usd_limit, self.monthly_usd_limit))

    def _estimate(self, role: str, est_tokens_in: int, grounded: bool) -> float:
        """Upper-ish cost of one call, reserved before sending: prompt + reserve_output_tokens (+ grounding fee)."""
        cost = self._cost(role, max(est_tokens_in, 1), 0 if role == "embed" else self.reserve_output_tokens, grounded)
        if cost is None:
            if self._caps_active():  # fail closed: a USD cap without prices would never trigger
                raise LLMConfigError(
                    f"Gemini spend caps are set but providers.yaml llm.prices_per_million_tokens.{role} is null")
            return 0.0
        return cost

    def _blocked(self, exc: LLMBudgetExceeded) -> LLMBudgetExceeded:
        """Remember the latest refusal: callers that fail soft (worker tasks) check it to requeue their work."""
        self.last_block = (time.monotonic(), exc)
        log.warning("gemini call refused: %s", exc)
        return exc

    def _store(self) -> Any:
        return self.usage_store if self.usage_store is not None else self._local_store

    def _periods(self) -> list[tuple[str, str, float | None]]:
        from screener.budget import this_month, today

        return [("day", today(), self.daily_usd_limit), ("month", this_month(), self.monthly_usd_limit)]

    def _reserve(self, estimate: float) -> list[str]:
        """Atomically reserve `estimate` USD on the shared day and month counters (and this run's tally)."""
        from screener.budget import SPEND_KEY

        if self.run_budget_usd is not None and self.spent_usd + estimate > self.run_budget_usd:
            raise self._blocked(LLMBudgetExceeded(
                f"RUN_BUDGET_USD=${self.run_budget_usd:g} reached (${self.spent_usd:.2f} spent by this process)", "run"))
        store, taken = self._store(), []
        for scope, period, limit in self._periods():
            if not store.try_consume(SPEND_KEY, period, estimate, limit):
                for p in taken:
                    store.release(SPEND_KEY, p, estimate)
                used = store.used(SPEND_KEY, period)
                raise self._blocked(LLMBudgetExceeded(
                    f"Gemini {scope} spend cap ${limit:g} reached (${used:.2f} used in {period})", scope))
            taken.append(period)
        self.spent_usd += estimate
        return taken

    def _settle(self, periods: list[str], estimate: float, actual: float) -> None:
        """Replace the reservation with the actual cost (never blocked: the money is already spent)."""
        from screener.budget import SPEND_KEY

        delta = actual - estimate
        self.spent_usd += delta
        if delta == 0:
            return
        store = self._store()
        for period in periods:
            try:
                if delta > 0:
                    store.try_consume(SPEND_KEY, period, delta, None)
                else:
                    store.release(SPEND_KEY, period, -delta)
            except Exception as exc:  # counting must never break a call
                log.debug("spend settle failed for %s: %s", period, exc)

    def usd_remaining(self) -> float | None:
        """USD left under the tightest cap (run, day, month); None when no USD cap is set."""
        from screener.budget import SPEND_KEY

        lefts = []
        if self.run_budget_usd is not None:
            lefts.append(self.run_budget_usd - self.spent_usd)
        store = self._store()
        for _, period, limit in self._periods():
            if limit is not None:
                lefts.append(limit - store.used(SPEND_KEY, period))
        return max(0.0, min(lefts)) if lefts else None

    def spend_summary(self) -> dict[str, tuple[float, float | None]]:
        """{'day': (used, cap), 'month': (used, cap)} from the shared counters."""
        from screener.budget import SPEND_KEY

        store = self._store()
        return {scope: (store.used(SPEND_KEY, period), limit) for scope, period, limit in self._periods()}

    def _thinking(self, role: str) -> types.ThinkingConfig | None:
        level = self.thinking_level.get(role)
        return types.ThinkingConfig(thinking_level=level.upper()) if level else None

    # --- structured output -------------------------------------------------------------------------

    async def generate_json(
        self,
        prompt: str,
        schema: type[M],
        model: Literal["fast", "smart"] = "fast",
        *,
        system: str | None = None,
        use_cache: bool = True,
    ) -> M:
        name = self.model_name(model)
        key = {"model": name, "prompt": prompt, "system": system, "schema": schema.model_json_schema()}
        if use_cache:
            cached = self.cache.get("generate_json", key)
            if cached is not None:
                await arecord(CallRecord("gemini", f"generate_json:{name}", "ok", cache_hit=True))
                return schema.model_validate(cached)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            system_instruction=system,
            thinking_config=self._thinking(model),
        )
        response = await self.call(
            model, "generate_json",
            lambda: self.client.aio.models.generate_content(model=name, contents=prompt, config=config),
            est_tokens_in=_approx_tokens(prompt, system),
        )
        parsed = response.parsed if isinstance(response.parsed, schema) else schema.model_validate_json(response.text or "")
        self.cache.set("generate_json", key, parsed.model_dump(mode="json"))
        return parsed

    # --- grounding ---------------------------------------------------------------------------------

    async def _grounded(self, prompt: str) -> types.GenerateContentResponse:
        name = self.model_name("fast")
        config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.0,
                                             thinking_config=self._thinking("fast"))
        return await self.call(
            "fast", "grounded",
            lambda: self.client.aio.models.generate_content(model=name, contents=prompt, config=config),
            est_tokens_in=_approx_tokens(prompt),
        )

    async def probe_grounding(self) -> bool:
        """One Google Search grounded call; result cached for the life of this client (one run)."""
        if self._grounding is not None:
            return self._grounding
        try:
            response = await self._grounded(GROUNDING_PROBE_PROMPT)
            meta = response.candidates[0].grounding_metadata if response.candidates else None
            self._grounding = bool(meta and meta.grounding_chunks)
            self.grounding_reason = "grounding chunks returned" if self._grounding else "no grounding metadata returned"
        except Exception as exc:  # any failure means grounding is unavailable for this run
            self._grounding = False
            self.grounding_reason = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.info("gemini grounding available=%s (%s)", self._grounding, self.grounding_reason)
        return self._grounding

    async def grounded_search(self, query: str, n: int = 10) -> list[dict[str, str]]:
        """[{title, url, snippet}] from grounding metadata. Empty if the probe failed."""
        if not await self.probe_grounding():
            return []
        response = await self._grounded(
            f"Search the web and list the most relevant sources for: {query}. Prefer primary sources."
        )
        meta = response.candidates[0].grounding_metadata if response.candidates else None
        if not meta or not meta.grounding_chunks:
            return []
        snippets: dict[int, list[str]] = {}
        for support in meta.grounding_supports or []:
            text = (support.segment.text if support.segment else "") or ""
            for idx in support.grounding_chunk_indices or []:
                snippets.setdefault(idx, []).append(text)
        results: list[dict[str, str]] = []
        for idx, chunk in enumerate(meta.grounding_chunks[:n]):
            web = chunk.web
            if not web or not web.uri:
                continue
            url = await self._resolve_redirect(web.uri)
            if url is None:
                continue
            results.append({"title": web.title or web.domain or "", "url": url, "snippet": " ".join(snippets.get(idx, []))[:500]})
        return results

    async def _resolve_redirect(self, uri: str) -> str | None:
        """Grounding returns vertexaisearch redirect links; read the Location header (no page fetch)."""
        if "grounding-api-redirect" not in uri:
            return normalize_url(uri)
        try:
            resp = await self._redirects.get(uri)
            location = resp.headers.get("location")
            return normalize_url(location) if location else None
        except httpx.HTTPError:
            return None


_client: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client
