"""Text embeddings: Gemini in batches by default, fastembed (BAAI/bge-small-en-v1.5, CPU) as fallback.

Vectors from different models are never mixed: one embed() call returns vectors from a single model,
and once Gemini fails the Embedder stays on fastembed for the rest of the run.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from screener.cache import DiskCache, get_cache
from screener.config import AppConfig, get_config
from screener.telemetry import CallRecord, arecord, timed

log = logging.getLogger(__name__)

FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"


@dataclass
class EmbedResult:
    model: str
    vectors: list[list[float]]


class Embedder:
    def __init__(self, cfg: AppConfig | None = None, gemini: Any = None, cache: DiskCache | None = None) -> None:
        self.cfg = cfg or get_config()
        self.gemini = gemini
        self.cache = cache or get_cache("embed")
        self.batch_size = int(self.cfg.providers.get("llm", {}).get("embed_batch_size", 100))
        self.fallen_back = gemini is None
        self._fastembed: Any = None

    async def embed(self, texts: list[str], task: str = "RETRIEVAL_DOCUMENT") -> EmbedResult:
        if not texts:
            return EmbedResult(model="", vectors=[])
        if not self.fallen_back:
            try:
                return await self._embed_gemini(texts, task)
            except Exception as exc:
                log.warning("gemini embeddings failed, switching to fastembed for this run: %s", exc)
                self.fallen_back = True
        return await self._embed_fastembed(texts)

    async def _embed_gemini(self, texts: list[str], task: str) -> EmbedResult:
        from google.genai import types

        model = self.gemini.model_name("embed")
        kind = f"{model}:{task}"
        vectors: list[list[float] | None] = [self.cache.get(kind, t) for t in texts]
        missing = [i for i, v in enumerate(vectors) if v is None]
        for start in range(0, len(missing), self.batch_size):
            idx = missing[start : start + self.batch_size]
            batch = [texts[i] for i in idx]
            # One Content per text: a plain list[str] is treated as ONE multi-part input (one vector back).
            contents = [types.Content(parts=[types.Part(text=t)]) for t in batch]
            response = await self.gemini.call(
                "embed", "embed_content",
                lambda contents=contents: self.gemini.client.aio.models.embed_content(
                    model=model, contents=contents, config=types.EmbedContentConfig(task_type=task)
                ),
                est_tokens_in=sum(len(t) for t in batch) // 4 + 1,
            )
            got = response.embeddings or []
            if len(got) != len(batch):
                raise RuntimeError(f"{model} returned {len(got)} embeddings for {len(batch)} texts")
            for i, emb in zip(idx, got, strict=True):
                vectors[i] = list(emb.values or [])
                self.cache.set(kind, texts[i], vectors[i])
        return EmbedResult(model=model, vectors=[v for v in vectors if v is not None])

    def _fastembed_model(self) -> Any:
        if self._fastembed is None:
            from fastembed import TextEmbedding

            cache_dir = self.cfg.settings.cache_dir / "fastembed_models"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._fastembed = TextEmbedding(model_name=FASTEMBED_MODEL, cache_dir=str(cache_dir))
        return self._fastembed

    async def _embed_fastembed(self, texts: list[str]) -> EmbedResult:
        kind = f"fastembed:{FASTEMBED_MODEL}"
        vectors: list[list[float] | None] = [self.cache.get(kind, t) for t in texts]
        missing = [i for i, v in enumerate(vectors) if v is None]
        if missing:
            with timed() as t:
                model = await asyncio.to_thread(self._fastembed_model)
                computed = await asyncio.to_thread(lambda: [v.tolist() for v in model.embed([texts[i] for i in missing])])
            await arecord(CallRecord("fastembed", FASTEMBED_MODEL, "ok", latency_ms=t["ms"]))
            for i, vec in zip(missing, computed, strict=True):
                vectors[i] = vec
                self.cache.set(kind, texts[i], vec)
        return EmbedResult(model=FASTEMBED_MODEL, vectors=[v for v in vectors if v is not None])
