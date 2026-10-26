"""Settings from .env plus the editable assumptions in config/*.yaml."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from screener import __version__

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
YAML_FILES = ("weights", "upgrad_gap_map", "exclusions", "seeds", "sources", "providers", "resolve", "enrich", "universe")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # Gemini
    gemini_api_key: SecretStr | None = None
    gemini_model_fast: str | None = None
    gemini_model_smart: str | None = None
    gemini_embed_model: str | None = None

    # Database
    database_url: str | None = None
    database_admin_url: SecretStr | None = None
    pipeline_rw_password: SecretStr | None = None
    web_ro_password: SecretStr | None = None

    # Google Sheet
    google_service_account_json: SecretStr | None = None
    google_sheet_id: str | None = None

    # Search and fetch providers
    searxng_url: str | None = None
    exa_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    serpapi_api_key: SecretStr | None = None
    jina_api_key: SecretStr | None = None

    # Enrichment sources (later stages)
    github_token: SecretStr | None = None
    producthunt_token: SecretStr | None = None
    pdl_api_key: SecretStr | None = None

    # Universe registries
    data_gov_in_api_key: SecretStr | None = None
    dpiit_startups_csv: Path | None = None

    # Behaviour
    user_agent_contact: str | None = None
    run_budget_usd: float | None = 5.0  # hard Gemini spend cap per process; empty = none
    run_max_llm_calls: int = 2000
    log_level: str = "INFO"
    cache_dir: Path = ROOT / ".cache"

    @field_validator("*", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and v.strip() == "" else v

    @model_validator(mode="after")
    def _smart_defaults_to_fast(self) -> Settings:
        """One-model setups: leave GEMINI_MODEL_SMART empty and the fast model does both jobs."""
        if self.gemini_model_smart is None:
            self.gemini_model_smart = self.gemini_model_fast
        return self

    @property
    def user_agent(self) -> str:
        contact = f"; contact: {self.user_agent_contact}" if self.user_agent_contact else ""
        return f"upGradFounderScreener/{__version__} (research crawler{contact})"

    @property
    def robots_token(self) -> str:
        return "upGradFounderScreener"

    def service_account_info(self) -> dict[str, Any] | None:
        """GOOGLE_SERVICE_ACCOUNT_JSON may be a file path or the JSON itself."""
        if self.google_service_account_json is None:
            return None
        raw = self.google_service_account_json.get_secret_value().strip()
        if raw.startswith("{"):
            return json.loads(raw)
        return json.loads(Path(raw).expanduser().read_text(encoding="utf-8"))


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    enabled: bool
    reason: str


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    weights: dict[str, Any]
    gap_map: dict[str, Any]
    exclusions: dict[str, Any]
    seeds: dict[str, Any]
    sources: dict[str, Any]
    providers: dict[str, Any]
    resolve: dict[str, Any]
    enrich: dict[str, Any]
    universe: dict[str, Any]

    def snapshot(self) -> dict[str, Any]:
        """What runs.config_snapshot stores (no secrets)."""
        s = self.settings
        return {
            "weights": self.weights,
            "gap_map": self.gap_map,
            "exclusions": self.exclusions,
            "models": {
                "fast": s.gemini_model_fast or "",
                "smart": s.gemini_model_smart or "",
                "embed": s.gemini_embed_model or "",
            },
        }

    def provider_statuses(self) -> dict[str, ProviderStatus]:
        s = self.settings

        def key(name: str, value: Any, var: str) -> ProviderStatus:
            return ProviderStatus(name, value is not None, "configured" if value is not None else f"{var} not set")

        gemini_ok = s.gemini_api_key is not None
        out = [
            key("postgres", s.database_url, "DATABASE_URL"),
            key("searxng", s.searxng_url, "SEARXNG_URL"),
            ProviderStatus("ddgs", True, "no key needed"),
            key("exa", s.exa_api_key, "EXA_API_KEY"),
            key("tavily", s.tavily_api_key, "TAVILY_API_KEY"),
            key("serpapi", s.serpapi_api_key, "SERPAPI_API_KEY"),
            key("gemini", s.gemini_api_key, "GEMINI_API_KEY"),
            ProviderStatus(
                "gemini_grounding",
                gemini_ok,
                "enabled if the grounding probe passes" if gemini_ok else "GEMINI_API_KEY not set",
            ),
            ProviderStatus("jina", True, "key set" if s.jina_api_key else "keyless (lower rate limit)"),
            ProviderStatus(
                "playwright",
                importlib.util.find_spec("playwright") is not None,
                "installed" if importlib.util.find_spec("playwright") else "package missing",
            ),
            ProviderStatus(
                "fastembed",
                importlib.util.find_spec("fastembed") is not None,
                "installed" if importlib.util.find_spec("fastembed") else "package missing",
            ),
            ProviderStatus(
                "google_sheets",
                s.google_service_account_json is not None and s.google_sheet_id is not None,
                "configured"
                if s.google_service_account_json and s.google_sheet_id
                else "GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID required",
            ),
            key("github", s.github_token, "GITHUB_TOKEN"),
            key("producthunt", s.producthunt_token, "PRODUCTHUNT_TOKEN"),
            key("pdl", s.pdl_api_key, "PDL_API_KEY"),
        ]
        return {p.name: p for p in out}

    def enabled(self, provider: str) -> bool:
        status = self.provider_statuses().get(provider)
        return bool(status and status.enabled)

    def ttl_seconds(self, namespace: str) -> float | None:
        days = self.providers.get("cache", {}).get("ttl_days", {}).get(namespace)
        return None if days is None else float(days) * 86400


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    path = config_dir / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def load_config(settings: Settings | None = None, config_dir: Path = CONFIG_DIR) -> AppConfig:
    files = {name: load_yaml(name, config_dir) for name in YAML_FILES}
    return AppConfig(
        settings=settings or Settings(),
        weights=files["weights"],
        gap_map=files["upgrad_gap_map"],
        exclusions=files["exclusions"],
        seeds=files["seeds"],
        sources=files["sources"],
        providers=files["providers"],
        resolve=files["resolve"],
        enrich=files["enrich"],
        universe=files["universe"],
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
