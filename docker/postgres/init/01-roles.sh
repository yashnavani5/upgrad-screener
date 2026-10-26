#!/bin/sh
# Creates the application roles and the pgvector extension on first start of an empty data volume.
# Table/view grants are issued by Alembic migrations, not here (see docs/ARCHITECTURE.md section 8).
set -eu

: "${PIPELINE_RW_PASSWORD:?PIPELINE_RW_PASSWORD is required}"
: "${WEB_RO_PASSWORD:?WEB_RO_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" -v rw_pw="$PIPELINE_RW_PASSWORD" -v ro_pw="$WEB_RO_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE ROLE pipeline_rw LOGIN PASSWORD :'rw_pw';
CREATE ROLE web_ro LOGIN PASSWORD :'ro_pw';
ALTER ROLE web_ro SET default_transaction_read_only = on;

REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO pipeline_rw, web_ro;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO pipeline_rw;
GRANT USAGE ON SCHEMA public TO web_ro;
SQL
