# syntax=docker/dockerfile:1.7
# Multi-stage build: builder compiles everything, runtime is a slim production image.
# Build: docker build -f docker/Dockerfile -t app:latest .
#
# The `lending-markets-rating/` directory (a vendored TypeScript/Node CLI)
# is part of the tree and is copied in by `COPY . /workspace/app/` below.

ARG PYTHON_VERSION=3.13
ARG DEBIAN_VERSION=bookworm
ARG BUILDER_IMAGE=python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}

# --- Base: shared between builder and runtime ---
FROM ${BUILDER_IMAGE} AS python-base

RUN --mount=type=cache,id=apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=apt-lib,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    tini \
    ca-certificates \
    && rm -rf /var/log/* /tmp/* \
    && mkdir -p /workspace/app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Symlink node -> bun so node_modules/.bin shebangs work (e.g. litestar-vite-typegen)
COPY --from=oven/bun:latest /usr/local/bin/bun /usr/local/bin/bunx /usr/local/bin/
RUN ln -s /usr/local/bin/bun /usr/local/bin/node

# --- Builder: install deps and compile assets ---
FROM python-base AS builder

ENV UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS="never" \
    UV_PYTHON=python3 \
    PATH="/workspace/app/.venv/bin:/usr/local/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN --mount=type=cache,id=apt-cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=apt-lib,target=/var/lib/apt,sharing=locked \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/log/* /tmp/*

WORKDIR /workspace/app

# Dependency files first for better layer caching
COPY pyproject.toml uv.lock package.json bun.lock README.md ./
COPY components.json biome.json vite.config.ts tsconfig.json ./

RUN bun install --frozen-lockfile

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv sync --all-extras --frozen --no-install-project --no-editable

COPY . /workspace/app/

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv \
    uv run certora-risk-api assets build \
    && cp resources/index.html src/cert_ra/api/domain/web/public/index.html \
    && uv build

# --- Runtime: minimal image, no build tools ---
FROM python-base AS runtime

ARG LITESTAR_APP="cert_ra.api.asgi:create_app"

ENV PATH="/workspace/app/.venv/bin:/usr/local/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    UV_PYTHON_DOWNLOADS="never" \
    UV_PYTHON=/workspace/app/.venv/bin/python \
    PYTHONHASHSEED=random \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    LITESTAR_APP="${LITESTAR_APP}"

RUN groupadd --system --gid 65532 nonroot \
    && useradd --no-create-home --system --uid 65532 --gid 65532 nonroot \
    && mkdir -p /workspace/app \
    && chown -R nonroot:nonroot /workspace

COPY --from=builder --chown=65532:65532 /workspace/app/dist/*.whl /tmp/
COPY --from=builder --chown=65532:65532 /workspace/app/.venv /workspace/app/.venv

WORKDIR /workspace/app

RUN uv pip install --disable-pip-version-check --no-deps /tmp/*.whl \
    && chown -R nonroot:nonroot /workspace/app \
    && rm -rf /var/log/* /tmp/*

# --- Market-metrics yarn toolchain (metrics worker) ---
# The metrics worker shells out to the `lending-markets-rating` package via
# `yarn <protocol>` (see cert_ra.metrics.market.yarn). It pins
# `yarn@1.22.19` and runs `node --import tsx`, so the runtime image needs a real
# Node.js + Yarn classic — the bun-as-node symlink from python-base is only used
# for the builder's asset compilation and can't run this subprocess.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /opt/yarn-v1.22.22 /opt/yarn-v1.22.22
RUN ln -sf /opt/yarn-v1.22.22/bin/yarn /usr/local/bin/yarn \
    && ln -sf /opt/yarn-v1.22.22/bin/yarnpkg /usr/local/bin/yarnpkg

# lending-markets-rating source plus its installed deps. node_modules is
# .dockerignore'd, so we install fresh here from the committed yarn.lock.
COPY --from=builder --chown=65532:65532 /workspace/app/lending-markets-rating /workspace/app/lending-markets-rating
RUN cd /workspace/app/lending-markets-rating \
    && yarn install --frozen-lockfile --non-interactive \
    && yarn cache clean \
    && chown -R nonroot:nonroot /workspace/app/lending-markets-rating

# Point the worker's yarn wrapper (CERT_RA_MARKET_METRICS_YARN_CWD) at the
# in-image package. The ANTHROPIC_API_KEY / THE_GRAPH_API_KEY it reads are
# inherited from the worker process env (already injected by infra).
ENV CERT_RA_MARKET_METRICS_YARN_CWD=/workspace/app/lending-markets-rating

USER nonroot

# Stamp the build's git commit so the running container can report which
# release it is (surfaced on the About page via CERT_RA_APP_COMMIT_SHA).
# Kept last so the per-build value doesn't invalidate the install layers above.
ARG COMMIT_SHA=""
ENV CERT_RA_APP_COMMIT_SHA=${COMMIT_SHA}

STOPSIGNAL SIGTERM
EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["litestar", "run", "--host", "0.0.0.0", "--port", "8000"]
