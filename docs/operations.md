# Operations Guide

## Environment Variables

All configuration is via environment variables. The complete reference is in
`docker/.env.docker.example`. Key variables:

| Variable                          | Required | Default          | Notes                                             |
| --------------------------------- | -------- | ---------------- | ------------------------------------------------- |
| `CERT_RA_DB_URL`                  | no\*     | —                | `postgresql+asyncpg://user:pass@host:5432/db`. If unset, built from `DATABASE_HOST` / `DATABASE_PORT` / `DATABASE_USER` / `DATABASE_PASSWORD` / `DATABASE_NAME` (the form Fargate task definitions inject). |
| `CERT_RA_DB_SSL_MODE`             | no       | `require`        | `disable` / `require` / `verify-ca` / `verify-full` (libpq vocabulary). `require` matches RDS `force_ssl=1` without a CA bundle. |
| `CERT_RA_DB_SSL_CA_PATH`          | no       | —                | PEM CA bundle. Required for `verify-ca` / `verify-full`. RDS: <https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem>. |
| `CERT_RA_APP_SECRET_KEY`          | yes      | —                | Random 32-byte secret                             |
| `CERT_RA_DUNE_API_KEY`            | yes      | —                | Required for automatic metrics                    |
| `CERT_RA_TEMPORAL_HOST`           | yes      | `localhost:7233` | Temporal server address                           |
| `CERT_RA_TEMPORAL_NAMESPACE`      | yes      | `default`        |                                                   |
| `CERT_RA_TEMPORAL_API_KEY`        | no       | —                | Set for Temporal Cloud; enables TLS automatically |
| `CERT_RA_TEMPORAL_ALERTS_ENABLED` | no       | `true`           | Set `false` to disable alert evaluation           |
| `ETHEREUM_BEACON_TOKEN`           | no       | —                | Required for Ethereum finality metrics            |

## Temporal Cloud

To connect to Temporal Cloud instead of a self-hosted server:

```bash
# docker/.env or .env
CERT_RA_TEMPORAL_HOST=<account-id>.tmprl.cloud:7233
CERT_RA_TEMPORAL_NAMESPACE=<namespace>.<account-id>
CERT_RA_TEMPORAL_API_KEY=<your-cloud-api-key>
```

TLS is enabled automatically when `CERT_RA_TEMPORAL_API_KEY` is set.

## Temporal Schedules

Each automatic metric group registers one Temporal Schedule per chain/token
combination on first worker startup. To manage schedules:

```bash
# List all schedules
temporal schedule list --namespace default

# Pause a schedule (e.g. temporarily stop TVL fetching for Ethereum)
temporal schedule pause --schedule-id tvl-ethereum --namespace default

# Resume
temporal schedule unpause --schedule-id tvl-ethereum --namespace default

# Trigger a schedule immediately
temporal schedule trigger --schedule-id tvl-ethereum --namespace default

# Delete a schedule
temporal schedule delete --schedule-id tvl-ethereum --namespace default
```

Schedule IDs follow the pattern `<metric-group>-<chain>` (e.g. `finality-ethereum`,
`throughput-arbitrum`, `tvl-base`, `token-activity-ethereum-usdc`,
`governance-arbitrum-proposals`).

## Workers

Two worker processes run alongside the API:

| Entry point                   | Env var / command    | Schedules managed                        |
| ----------------------------- | -------------------- | ---------------------------------------- |
| `certora-risk-metrics-worker` | `CERT_RA_TEMPORAL_*` | All automatic metric schedules           |
| `certora-risk-alerts-worker`  | `CERT_RA_TEMPORAL_*` | Alert evaluation + notification dispatch |

In Docker: `make docker-workers` starts both.

To scale horizontally, run multiple instances of either worker pointing at the
same Temporal server and namespace — Temporal distributes work across all pollers
automatically. Schedules are only created once (idempotent on startup).

## Database

### Migrations

Apply on every deploy:

```bash
uv run certora-risk-api database upgrade
```

The Docker Compose `migrator` service runs this automatically before the app
starts.

### Backup considerations

The `tvl`, `token_activity`, `governance_event`, `throughput`, `decentralization`,
`finality_*`, and `time_to_finality` tables are time-series data that grows
continuously. Back up the full database regularly. For point-in-time recovery,
enable WAL archiving in Postgres.

`manual_metric` is configuration data seeded from `scripts/seed_manual_metrics.csv`
— it can be rebuilt from the CSV at any time via `make fill-manual`.

## Logging

The platform uses [structlog](https://www.structlog.org/) with JSON output in
production. Log level is controlled by:

```bash
CERT_RA_LOG_LEVEL=10  # DEBUG
CERT_RA_LOG_LEVEL=20  # INFO (default in production)
CERT_RA_LOG_LEVEL=30  # WARNING
```

To ship logs, configure your container runtime or sidecar to forward stdout JSON
to your log aggregator (Datadog, Loki, CloudWatch, etc.).

## Sentry

Optional error tracking:

```bash
CERT_RA_SENTRY_DSN=https://<key>@sentry.io/<project>
```

## RPC Endpoints

Free public RPC fallbacks are built in for all chains. Override with private
endpoints for better rate limits and reliability:

```bash
CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_1=https://eth-mainnet.g.alchemy.com/v2/<key>
CERT_RA_RPC_ARBITRUM_PRIVATE_RPC_1=https://arb-mainnet.g.alchemy.com/v2/<key>
CERT_RA_RPC_BASE_PRIVATE_RPC_1=https://base-mainnet.g.alchemy.com/v2/<key>
CERT_RA_RPC_POLYGON_PRIVATE_RPC_1=https://polygon-mainnet.g.alchemy.com/v2/<key>
CERT_RA_RPC_SOLANA_PRIVATE_RPC_1=https://mainnet.helius-rpc.com/?api-key=<key>
CERT_RA_RPC_AVALANCHE_PRIVATE_RPC_1=https://api.avax.network/ext/P
# Ink and Unichain require optimism_syncStatus support:
CERT_RA_RPC_INK_URL=https://<quicknode-ink-endpoint>
CERT_RA_RPC_UNICHAIN_URL=https://<unichain-endpoint>
```

## Sign-in provider enforcement

Per-team `enforced_provider` (gated by `CERT_RA_FEATURES_ENFORCED_PROVIDER`,
default off) locks a team's members to a single OIDC provider.

**Known limitation — multi-team users.** Each user has at most one linked
OIDC provider. If a user belongs to two teams that enforce *different*
providers, they cannot satisfy both — migrating to one locks them out of
the other's sign-in. Before a broad rollout, surface at-risk users with
`cert_ra.api.lib.team_policy.find_conflicting_enforcement_users(db)` (one
row per user in 2+ teams with conflicting enforced providers) and resolve
them out-of-band. Stuck-member reminders are throttled to one email per
member per 48h.
