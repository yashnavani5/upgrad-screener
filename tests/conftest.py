from __future__ import annotations

import os
from pathlib import Path

import pytest

from screener.config import AppConfig, Settings, load_config


@pytest.fixture(autouse=True)
def _no_telemetry_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never write api_calls rows to the developer's database."""
    monkeypatch.setattr("screener.telemetry._persist", lambda rec: None)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings isolated from the developer's .env."""
    return Settings(_env_file=None, cache_dir=tmp_path / "cache", user_agent_contact="tests@example.invalid")


@pytest.fixture
def cfg(settings: Settings) -> AppConfig:
    return load_config(settings)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="set TEST_DATABASE_URL to a Postgres URL to run")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)
