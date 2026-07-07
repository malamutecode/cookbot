# Feedek / cookbot-tastyhub backend — Cloud Run image.
#
# Build context MUST be the repo root (not clients/tastyhub), because the client's
# uv.lock pins two editable path deps at ../../packages/{cookbot-core,delivery-shops}.
# The image reproduces that monorepo layout under /app so uv resolves them:
#   /app/packages/cookbot-core
#   /app/packages/delivery-shops
#   /app/clients/tastyhub   (WORKDIR)
#
# Build from repo root:
#   docker build -f clients/tastyhub/Dockerfile -t cookbot-tastyhub .
FROM python:3.12-slim AS base

# uv: fast, reproducible installs (pinned copy from the official image).
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Copy only dependency manifests first so the (slow) dependency layer is cached
#    and only rebuilt when a pyproject/lock changes — not on every source edit.
COPY clients/tastyhub/pyproject.toml clients/tastyhub/uv.lock ./clients/tastyhub/
COPY packages/cookbot-core/pyproject.toml ./packages/cookbot-core/
COPY packages/delivery-shops/pyproject.toml ./packages/delivery-shops/

# The editable path deps must exist for uv to resolve the graph, even for a
# deps-only sync. Provide minimal package skeletons so the manifests import.
RUN mkdir -p packages/cookbot-core/cookbot packages/delivery-shops/delivery_shops \
    && touch packages/cookbot-core/cookbot/__init__.py packages/delivery-shops/delivery_shops/__init__.py

WORKDIR /app/clients/tastyhub
# Install third-party deps only (no project code yet) into a project venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) Now copy the real source for all three packages and finish the install
#    (this re-links the editable local packages against their actual code).
WORKDIR /app
COPY packages/cookbot-core   ./packages/cookbot-core
COPY packages/delivery-shops ./packages/delivery-shops
COPY clients/tastyhub        ./clients/tastyhub

WORKDIR /app/clients/tastyhub
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Cloud Run injects $PORT (defaults to 8080). Bind 0.0.0.0 so the container is reachable.
ENV PORT=8080
EXPOSE 8080

# Use the project venv's uvicorn directly (no `uv run` overhead at boot).
# Shell form so ${PORT} expands at runtime.
CMD exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
