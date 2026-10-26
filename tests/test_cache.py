from __future__ import annotations

from pathlib import Path

from screener.cache import DiskCache, cache_key


class FakeClock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_roundtrip_and_miss(tmp_path: Path) -> None:
    c = DiskCache(tmp_path, "fetch", ttl_seconds=60)
    assert c.get("page", "https://a.example") is None
    c.set("page", "https://a.example", {"html": "<p>x</p>"})
    assert c.get("page", "https://a.example") == {"html": "<p>x</p>"}


def test_namespaces_are_isolated(tmp_path: Path) -> None:
    fetch = DiskCache(tmp_path, "fetch", ttl_seconds=None)
    search = DiskCache(tmp_path, "search", ttl_seconds=None)
    fetch.set("k", "same-key", 1)
    search.set("k", "same-key", 2)
    assert fetch.get("k", "same-key") == 1
    assert search.get("k", "same-key") == 2
    assert (tmp_path / "fetch").is_dir() and (tmp_path / "search").is_dir()


def test_kind_is_part_of_the_key(tmp_path: Path) -> None:
    c = DiskCache(tmp_path, "llm", ttl_seconds=None)
    c.set("a", "k", 1)
    assert c.get("b", "k") is None
    assert cache_key("a", "k") != cache_key("b", "k")


def test_ttl_expiry(tmp_path: Path) -> None:
    clock = FakeClock()
    c = DiskCache(tmp_path, "search", ttl_seconds=3 * 86400, clock=clock)
    c.set("results", "q", ["r"])
    clock.t += 3 * 86400 - 1
    assert c.get("results", "q") == ["r"]
    clock.t += 2
    assert c.get("results", "q") is None


def test_no_ttl_never_expires(tmp_path: Path) -> None:
    clock = FakeClock()
    c = DiskCache(tmp_path, "llm", ttl_seconds=None, clock=clock)
    c.set("gen", "prompt", {"a": 1})
    clock.t += 10 * 365 * 86400
    assert c.get("gen", "prompt") == {"a": 1}


def test_dict_keys_are_order_independent() -> None:
    assert cache_key("k", {"a": 1, "b": 2}) == cache_key("k", {"b": 2, "a": 1})


def test_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    c = DiskCache(tmp_path, "fetch", ttl_seconds=None)
    c.set("page", "u", "ok")
    digest = cache_key("page", "u")
    (tmp_path / "fetch" / digest[:2] / f"{digest}.json").write_text("{not json", encoding="utf-8")
    assert c.get("page", "u") is None
