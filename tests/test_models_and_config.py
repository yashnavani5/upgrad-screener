from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from screener.config import AppConfig, Settings, load_config
from screener.db import DbQuotaStore, init_db
from screener.models import Base, Claim, Company

SPEC_TABLES = {
    "companies", "founders", "company_founders", "rounds", "claims", "candidates", "scores",
    "exclusions", "feedback", "runs", "api_calls", "run_funnel", "provider_quota", "discoveries", "company_aliases", "resolution_pairs", "judgments", "registry_entities", "prospects", "tasks", "load_checkpoints", "policy_versions",
}


def sqlite_engine():
    return create_engine("sqlite:///:memory:")


def test_all_spec_tables_exist_on_sqlite() -> None:
    engine = sqlite_engine()
    steps = init_db(engine)
    assert "sqlite" in steps[0]
    assert set(inspect(engine).get_table_names()) == SPEC_TABLES


def test_claim_roundtrip_with_json_value() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    company_id = uuid.uuid4()
    with Session(engine) as s:
        s.add(Company(id=company_id, name="Example Co", embedding=[0.1, 0.2]))
        s.add(Claim(
            entity_type="company", entity_id=company_id, field="total_raised_usd", value_json={"amount": 1_000_000},
            source_url="https://example.com/news", source_type="press",
            retrieved_at=datetime.now(timezone.utc), evidence_span="raised $1 million",
        ))
        s.commit()
        claim = s.query(Claim).one()
        assert claim.value_json == {"amount": 1_000_000}
        assert claim.verified_status == "unverified"
        assert s.get(Company, company_id).embedding == [0.1, 0.2]


def test_db_quota_store_upsert_guard_on_sqlite() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    q = DbQuotaStore(engine)
    assert q.try_consume("serpapi", "2026-10", 1, 2)
    assert q.try_consume("serpapi", "2026-10", 1, 2)
    assert not q.try_consume("serpapi", "2026-10", 1, 2)
    assert q.used("serpapi", "2026-10") == 2
    q.release("serpapi", "2026-10", 1)
    assert q.try_consume("serpapi", "2026-10", 1, 2)
    assert q.try_consume("exa", "2026-10", 5, None)  # no limit configured
    assert not q.try_consume("tavily", "2026-10", 3, 2)  # single call larger than the limit


def test_empty_env_values_are_none(tmp_path) -> None:
    s = Settings(_env_file=None, gemini_api_key="", exa_api_key="  ", searxng_url="http://localhost:8080", cache_dir=tmp_path)
    assert s.gemini_api_key is None and s.exa_api_key is None
    assert s.searxng_url == "http://localhost:8080"


def test_provider_status_reflects_keys(cfg: AppConfig) -> None:
    st = cfg.provider_statuses()
    assert not st["exa"].enabled and st["exa"].reason == "EXA_API_KEY not set"
    assert st["ddgs"].enabled
    assert not st["gemini_grounding"].enabled
    s = Settings(_env_file=None, exa_api_key="k", gemini_api_key="g")
    st2 = load_config(s).provider_statuses()
    assert st2["exa"].enabled and st2["gemini_grounding"].enabled


def test_yaml_config_loaded_and_ttls(cfg: AppConfig) -> None:
    assert cfg.weights["final"]["exponents"]["T"] == 0.30
    assert cfg.ttl_seconds("fetch") == 7 * 86400
    assert cfg.ttl_seconds("search") == 3 * 86400
    assert cfg.ttl_seconds("llm") is None
    assert set(cfg.providers["search"]["strategies"]) == {"cheap", "semantic", "quality"}


def test_service_account_inline_or_path(tmp_path) -> None:
    inline = Settings(_env_file=None, google_service_account_json='{"client_email": "a@b.iam"}')
    assert inline.service_account_info() == {"client_email": "a@b.iam"}
    path = tmp_path / "sa.json"
    path.write_text('{"client_email": "c@d.iam"}', encoding="utf-8")
    from_path = Settings(_env_file=None, google_service_account_json=str(path))
    assert from_path.service_account_info() == {"client_email": "c@d.iam"}


def test_user_agent_is_descriptive(settings: Settings) -> None:
    assert settings.user_agent.startswith("upGradFounderScreener/")
    assert "tests@example.invalid" in settings.user_agent
