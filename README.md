# Certora Blockchain Risk Assessment

A risk-monitoring platform for blockchain networks and tokens built with **Litestar**, **Inertia.js + React 19**, **PostgreSQL**, and **Temporal**.

Supported chains: Ethereum, Arbitrum, Base, Ink, Unichain, Optimism, Polygon, Solana, Avalanche.

Metrics tracked per chain include TVL, finality, throughput, decentralization coefficients, governance activity, and token-level activity (USDC, USDT0, WETH, USDe, AAVE, UNI).

## Prerequisites

| Tool                             | Version     | Notes                                              |
| -------------------------------- | ----------- | -------------------------------------------------- |
| Python                           | 3.12 – 3.14 | Managed by `uv`                                    |
| [uv](https://docs.astral.sh/uv/) | latest      | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [bun](https://bun.sh)            | latest      | `curl -fsSL https://bun.sh/install \| bash`        |
| Docker                           | 24+         | For local Postgres + Temporal                      |
| [direnv](https://direnv.net)     | optional    | Auto-exports `.env` on `cd`                        |

## Quick Start — Local Dev

```bash
# 1. Clone and enter repo
git clone git@github.com:Certora/risk-assessment.git
cd risk-assessment

# 2. Copy env template and fill in at minimum CERT_RA_DUNE_API_KEY
cp .env.example .env
$EDITOR .env

# 3. Install Python + JS dependencies
make install

# 4. Start Postgres (and optionally Temporal) via Docker
docker compose -f docker/docker-compose-db.yml up -d
# — or start the full infra stack including Temporal —
make docker-infra

# 5. If using a local Temporal server instead of Docker
temporal server start-dev

# 6. Apply database migrations
uv run certora-risk-api database upgrade

# 7. (Optional) Seed manual metrics from CSV
make fill-manual

# 8. Build frontend assets
uv run certora-risk-api assets build

# 9. Start the API server (http://localhost:8000)
uv run certora-risk-api run

# 10. (Optional) Start Vite dev server for hot-reload frontend
make dev-js
```

## Quick Start — Docker

```bash
# 1. Copy Docker env template
cp docker/.env.docker.example docker/.env

# 2. Start infrastructure (Postgres + Temporal + Temporal UI)
make docker-infra

# 3. Start the API (includes DB migrations)
make docker-api

# 4. Start metrics and alerts workers
make docker-workers
```

**URLs:**

| Service     | URL                   |
| ----------- | --------------------- |
| App         | http://localhost:8000 |
| Temporal UI | http://localhost:8080 |
| Postgres    | localhost:5432        |

## The metrics worker

The `metrics-worker` service (defined in `docker/docker-compose.yml`) runs the
Temporal worker that ingests chain metrics. Because the compose file lives in
`docker/`, pass `-f docker/docker-compose.yml` (or run the commands from the
`docker/` directory).

```bash
# Stop the worker
docker compose -f docker/docker-compose.yml stop metrics-worker

# Start the worker
docker compose -f docker/docker-compose.yml start metrics-worker
```

**After changing `.env` secrets**, a plain `start`/`restart` will _not_ re-read
the env files. Recreate the container instead:

```bash
docker compose -f docker/docker-compose.yml up -d --force-recreate metrics-worker
```

The worker loads `../.env` then `.env.docker.example` (later overrides earlier),
so new secrets in `../.env` only take effect on a `--force-recreate`. The sibling
`alerts-worker` service behaves the same way.

## Seeding Data

```bash
# Seed manual metrics (e.g. governance scores, upgrade transparency) from CSV
make fill-manual

# Insert synthetic dummy data for all automatic metric tables (dev/testing only)
make fill-dummy
```

## Common Commands

```bash
make check          # lint + test (default CI target)
make lint           # ruff, mypy, pyright, slotscheck, biome
make fmt            # auto-fix: ruff-fix + ruff-fmt + biome-fix
make test           # fast parallel test suite
make coverage       # with HTML coverage report
make build-js       # Vite production build
make docker-infra   # start db + temporal + temporal-ui
make docker-api     # + app
make docker-workers # + metrics-worker + alerts-worker
```

## Configuration

| File                         | Used for                           |
| ---------------------------- | ---------------------------------- |
| `.env.example`               | Local development — copy to `.env` |
| `docker/.env.docker.example` | Docker — copy to `docker/.env`     |

The single mandatory env var for metrics ingestion is:

```
CERT_RA_DUNE_API_KEY=<your-dune-api-key>
```

All other settings have defaults (see `.env.example` for the full list). Private RPC URLs (`CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_1`, etc.) are optional — free public fallbacks are built in.

For full settings reference see `src/cert_ra/settings/`.

## Documentation

Detailed development and operations guides are in [docs/](docs/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to build, test, and submit changes.

```bash
make lint      # must pass before submitting a PR
make test      # test suite
make coverage  # target: 90%+ on new code
```

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Please do not open a
public issue for security reports.

## License

Copyright © 2026 Certora.

This project is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0-only). See [LICENSE](LICENSE) for the full text. Because it is network
software, if you run a modified version and let users interact with it over a
network, AGPL section 13 requires you to offer them the corresponding source.

It was initially bootstrapped from the MIT-licensed
[litestar-fullstack-inertia](https://github.com/litestar-org/litestar-fullstack-inertia)
template; that notice is retained in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
