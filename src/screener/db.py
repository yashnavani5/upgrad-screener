"""Engine and sessions, `screener db init` (roles + Alembic migrations), and the provider quota store."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from screener.models import Base, ProviderQuota

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
APP_ROLES = ("pipeline_rw", "web_ro")
_UNREACHABLE_RETRY_SECONDS = 30.0


class DatabaseNotConfigured(RuntimeError):
    pass


def normalize_url(url: str) -> str:
    """Use psycopg 3 for plain postgres URLs."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": 5}
        # Workers run many tasks at once and some hold a session across network calls (default pool: 5 + 10).
        kwargs.update(pool_size=30, max_overflow=30, pool_timeout=60)
    return create_engine(url, **kwargs)


def is_postgres(engine: Engine) -> bool:
    return engine.dialect.name == "postgresql"


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_unreachable_until = 0.0


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        from screener.config import get_config

        url = get_config().settings.database_url
        if not url:
            raise DatabaseNotConfigured("DATABASE_URL is not set (see .env.example)")
        _engine = make_engine(url)
        _session_factory = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def set_engine(engine: Engine) -> None:
    """Point the process at a specific engine (tests, CLI overrides)."""
    global _engine, _session_factory, _unreachable_until
    _engine = engine
    _session_factory = sessionmaker(engine, expire_on_commit=False)
    _unreachable_until = 0.0


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def try_session() -> Iterator[Session | None]:
    """Yields a session, or None if the DB is not configured/unreachable (fail soft; re-tried after 30 s)."""
    global _unreachable_until
    if time.monotonic() < _unreachable_until:
        yield None
        return
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        _unreachable_until = time.monotonic() + _UNREACHABLE_RETRY_SECONDS
        log.warning("database unavailable: %s", str(exc).splitlines()[0] if str(exc) else exc)
        yield None
        return
    with session_scope() as session:
        yield session


def db_available() -> bool:
    with try_session() as s:
        return s is not None


# --- db init ---------------------------------------------------------------------------------------


def ensure_roles(admin_url: str, db_name: str, rw_password: str | None, ro_password: str | None) -> list[str]:
    """Idempotently create pipeline_rw / web_ro, the vector extension and schema ownership (needs superuser)."""
    import psycopg
    from psycopg import sql

    done: list[str] = []
    dsn = make_url(normalize_url(admin_url)).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # trigram search (v_search, migration 0012)
        for role, password in (("pipeline_rw", rw_password), ("web_ro", ro_password)):
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if not exists:
                if not password:
                    raise RuntimeError(f"role {role} missing and its password env var is not set")
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                done.append(f"created role {role}")
            elif password:
                conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                done.append(f"role {role} exists (password synced)")
            else:
                done.append(f"role {role} exists")
        conn.execute("ALTER ROLE web_ro SET default_transaction_read_only = on")
        db = sql.Identifier(db_name)
        conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(db))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO pipeline_rw, web_ro").format(db))
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute("ALTER SCHEMA public OWNER TO pipeline_rw")
        conn.execute("GRANT USAGE ON SCHEMA public TO web_ro")
        done.append("extensions vector + pg_trgm, schema ownership and CONNECT grants ensured")
    return done


def missing_roles(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        found = {r[0] for r in conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname IN ('pipeline_rw','web_ro')"))}
    return [r for r in APP_ROLES if r not in found]


def alembic_config(engine: Engine):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["engine"] = engine
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(alembic_config(engine), revision)


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(engine: Engine) -> str | None:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(alembic_config(engine)).get_current_head()


def init_db(engine: Engine | None = None, admin_url: str | None = None) -> list[str]:
    """Create roles (if an admin URL is given), then tables, views and grants. SQLite: tables only."""
    from screener.config import get_config

    settings = get_config().settings
    engine = engine or get_engine()
    steps: list[str] = []
    if not is_postgres(engine):
        Base.metadata.create_all(engine)
        return ["sqlite: created tables (views and roles are Postgres-only)"]

    admin = admin_url or (settings.database_admin_url.get_secret_value() if settings.database_admin_url else None)
    if admin:
        steps += ensure_roles(
            admin,
            engine.url.database or "screener",
            settings.pipeline_rw_password.get_secret_value() if settings.pipeline_rw_password else None,
            settings.web_ro_password.get_secret_value() if settings.web_ro_password else None,
        )
    else:
        missing = missing_roles(engine)
        steps.append(
            f"roles missing: {', '.join(missing)} (set DATABASE_ADMIN_URL to create them)" if missing else "roles present"
        )
    upgrade(engine)
    steps.append(f"migrations at {current_revision(engine)} (tables, v_* views, web_ro grants)")
    return steps


# --- provider quota --------------------------------------------------------------------------------


class QuotaStore(Protocol):
    def try_consume(self, provider: str, period: str, cost: float, limit: float | None) -> bool: ...
    def release(self, provider: str, period: str, cost: float) -> None: ...
    def used(self, provider: str, period: str) -> float: ...


@dataclass
class MemoryQuotaStore:
    """In-process quota store (tests)."""

    counts: dict[tuple[str, str], float]

    def __init__(self) -> None:
        self.counts = {}

    def try_consume(self, provider: str, period: str, cost: float, limit: float | None) -> bool:
        used = self.counts.get((provider, period), 0.0)
        if limit is not None and used + cost > limit:
            return False
        self.counts[(provider, period)] = used + cost
        return True

    def release(self, provider: str, period: str, cost: float) -> None:
        key = (provider, period)
        self.counts[key] = max(0.0, self.counts.get(key, 0.0) - cost)

    def used(self, provider: str, period: str) -> float:
        return self.counts.get((provider, period), 0.0)


class DbQuotaStore:
    """Atomic per-period counters in provider_quota (Postgres or SQLite upsert with a WHERE guard)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._insert = pg_insert if is_postgres(engine) else sqlite_insert

    def try_consume(self, provider: str, period: str, cost: float, limit: float | None) -> bool:
        if limit is not None and cost > limit:
            return False
        t = ProviderQuota.__table__
        stmt = self._insert(t).values(provider=provider, period=period, used=cost, limit=limit)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.c.provider, t.c.period],
            set_={"used": t.c.used + cost, "limit": limit},
            where=(t.c.used + cost <= limit) if limit is not None else None,
        ).returning(t.c.used)
        with self.engine.begin() as conn:
            return conn.execute(stmt).first() is not None

    def release(self, provider: str, period: str, cost: float) -> None:
        t = ProviderQuota.__table__
        with self.engine.begin() as conn:
            conn.execute(
                t.update()
                .where(t.c.provider == provider, t.c.period == period)
                .values(used=t.c.used - cost)
            )

    def used(self, provider: str, period: str) -> float:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(ProviderQuota.used).where(ProviderQuota.provider == provider, ProviderQuota.period == period)
            ).first()
        return float(row[0]) if row else 0.0
