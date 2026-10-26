"""Disk cache under .cache/, one directory per namespace, entries keyed by sha256 of (kind, key)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

NAMESPACES = ("fetch", "search", "llm", "embed", "robots")


def cache_key(kind: str, key: Any) -> str:
    """Stable sha256 of (kind, key). Dict/list keys are serialized with sorted keys."""
    raw = key if isinstance(key, str) else json.dumps(key, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(f"{kind}\x00{raw}".encode()).hexdigest()


class DiskCache:
    """JSON-file cache. `ttl_seconds=None` means entries never expire."""

    def __init__(
        self,
        root: Path,
        namespace: str,
        ttl_seconds: float | None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.dir = Path(root) / namespace
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds
        self._clock = clock

    def _path(self, digest: str) -> Path:
        return self.dir / digest[:2] / f"{digest}.json"

    def get(self, kind: str, key: Any) -> Any | None:
        path = self._path(cache_key(kind, key))
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if self.ttl_seconds is not None and self._clock() - entry["created_at"] > self.ttl_seconds:
            return None
        return entry["value"]

    def set(self, kind: str, key: Any, value: Any) -> None:
        path = self._path(cache_key(kind, key))
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"kind": kind, "created_at": self._clock(), "value": value}
        # Atomic replace so a crash never leaves a half-written entry.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, kind: str, key: Any) -> None:
        self._path(cache_key(kind, key)).unlink(missing_ok=True)


_caches: dict[str, DiskCache] = {}


def get_cache(namespace: str) -> DiskCache:
    """Namespace cache with root and TTL from config (fetch 7d, search 3d, llm/embed forever)."""
    if namespace not in NAMESPACES:
        raise ValueError(f"unknown cache namespace {namespace!r}; expected one of {NAMESPACES}")
    if namespace not in _caches:
        from screener.config import get_config

        cfg = get_config()
        _caches[namespace] = DiskCache(cfg.settings.cache_dir, namespace, cfg.ttl_seconds(namespace))
    return _caches[namespace]
