# Development Guide

## Prerequisites

| Tool                             | Install                                                           |
| -------------------------------- | ----------------------------------------------------------------- |
| Python 3.12+                     | `uv` manages this automatically                                   |
| [uv](https://docs.astral.sh/uv/) | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                |
| [bun](https://bun.sh)            | `curl -fsSL https://bun.sh/install \| bash`                       |
| Docker 24+                       | [docs.docker.com/get-docker](https://docs.docker.com/get-docker/) |
| [direnv](https://direnv.net)     | Optional — auto-exports `.env` on `cd`                            |

## Install

```bash
make install        # uv sync + bun install
```

## Local Dev (no Docker)

```bash
# 1. Copy env template and fill in CERT_RA_DUNE_API_KEY at minimum
cp .env.example .env && $EDITOR .env

# 2. Start a local Temporal dev server (no persistence; fine for development)
temporal server start-dev

# 3. Start Postgres via the lightweight Docker Compose file
docker compose -f docker/docker-compose.yml up db -d

# 4. Apply migrations
uv run certora-risk-api database upgrade

# 5. Build frontend assets (one-off)
uv run certora-risk-api assets build

# 6. Start the API
uv run certora-risk-api run

# 7. (Optional) Vite dev server for hot-reload frontend
make dev-js         # serves on http://localhost:5173; proxied by Litestar
```

The app is available at **http://localhost:8000**.

## Docker Dev (full stack)

```bash
cp docker/.env.docker.example docker/.env

make docker-infra   # Postgres + Temporal + Temporal UI (http://localhost:8080)
make docker-api     # + app (http://localhost:8000)
make docker-workers # + metrics-worker + alerts-worker

make docker-down    # tear down
```

## Seeding Data

```bash
# Seed manual metrics from scripts/seed_manual_metrics.csv (idempotent)
make fill-manual

# Insert synthetic data for all automatic metric tables (dev only)
make fill-dummy
```

## Testing

```bash
make test           # fast parallel suite (no coverage)
make test-all       # full suite
make coverage       # with HTML coverage report (opens htmlcov/index.html)
make pytest         # plain pytest run (no parallel)
```

Tests are function-based pytest. Integration tests run against a real Postgres
database started via `docker compose -f docker/docker-compose-db.yml`. Unit tests
are self-contained.

## Linting

```bash
make lint           # all checks (Python + JS)
make lint-py        # ruff, mypy, pyright, slotscheck, codespell
make lint-js        # biome
make fmt            # auto-fix: ruff-fix + ruff-fmt + biome-fix
```

## Type Generation

After modifying API response schemas, regenerate the TypeScript SDK:

```bash
uv run certora-risk-api assets generate-types
```

This writes to `resources/lib/api.ts`.

## Troubleshooting

**`temporal server start-dev` port conflict**

The dev server uses port 7233. If that port is taken:

```bash
temporal server start-dev --port 7234
# then set in .env:
# CERT_RA_TEMPORAL_HOST=localhost:7234
```

**Postgres reset**

```bash
docker compose -f docker/docker-compose-db.yml down -v  # drops volumes
docker compose -f docker/docker-compose-db.yml up -d
uv run certora-risk-api database upgrade
```

**Temporal UI**

When using Docker infra: http://localhost:8080
When using `temporal server start-dev`: http://localhost:8233

Browse workflows, schedules, and worker status. Pause or trigger schedules
manually from the **Schedules** tab.

**Migration generation**

```bash
uv run certora-risk-api database make-migrations   # generates a new Alembic revision
uv run certora-risk-api database upgrade           # applies pending migrations
```
