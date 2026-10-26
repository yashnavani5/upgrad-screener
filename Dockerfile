# Pipeline image: the `screener` CLI with Chromium for Playwright.
# Base pinned to the exact Playwright version in uv.lock, so its preinstalled browsers match.
FROM astral/uv:0.13.5 AS uv

FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    CACHE_DIR=/app/.cache

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock CLAUDE.md ./
RUN uv sync --frozen --no-dev --no-install-project --python python3.12

COPY src ./src
COPY config ./config
COPY contracts ./contracts
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --python python3.12 \
    && mkdir -p /app/.cache && chown -R pwuser:pwuser /app/.cache /app/config

ENV PATH="/app/.venv/bin:${PATH}"
USER pwuser
ENTRYPOINT ["screener"]
CMD ["--help"]
