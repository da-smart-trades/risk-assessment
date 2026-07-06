# Deployment

How to deploy cert-ra to AWS. Two flows: **initial setup** (run once
per env) and **routine upgrade** (every release).

The infrastructure is defined under [`infra/`](../infra/) as 11 CDK
stacks. The operator-facing entry points are the three scripts under
[`infra/scripts/`](../infra/scripts/) and the four GitHub Actions
workflows under [`.github/workflows/`](../.github/workflows/).

---

## Prerequisites

### One-time setup

- AWS account dedicated to cert-ra (staging and prod share one account
  with stack-name suffixes).
- AWS IAM Identity Center configured with two permission sets:
    * `CertRaInstaller` — full IaC privileges on every stack. Required
      for `initial-setup.sh` and for foundation-stack changes.
    * `CertRaUpgrader` — scoped to AppStack / WorkersStack /
      MigrationsStack only. Required for `upgrade.sh`.
- A parent DNS zone you control at your registrar (e.g. Cloudflare). The
  Route53 NS records emitted by `CertRa-DnsStack-${env}` must be added there to
  delegate the per-env subdomain. The prod/staging domains come from
  `deployment.config.json` → `environments.<env>.domain` (see
  `deployment.config.example.json`).
- `deployment.config.json` created from `deployment.config.example.json` with your
  AWS account/region, GitHub owner/repo, domains, and Route53 hosted-zone ids.
- GitHub repo variables configured (mirroring `deployment.config.json`):
    * `vars.AWS_ACCOUNT_ID`
    * `vars.AWS_REGION` (defaults to `us-east-1`)
- GitHub repo `prod` environment configured with reviewer protection
  (Settings → Environments → prod → Required reviewers).
- `lending-markets-rating/` is vendored in-tree; the Dockerfile COPYs its source.

### Local tools

Operators running scripts from a laptop need:

- `aws` CLI v2 with SSO configured (profiles
  `cert-ra-${env}-installer` + `cert-ra-${env}-upgrader`).
- `uv` (Python project sync).
- `bun` / `bunx` (CDK Toolkit invocation).
- `node` / `npm` (lending-markets-rating dependency install). The
  scripts run `npm install` in `lending-markets-rating/` before each
  deploy so the Node CLI's dependencies are present in the Docker build
  context. Required on every initial-setup AND upgrade run — `node_modules/`
  is gitignored so it has to be regenerated each time.
- `jq` (AppSpec generation in `upgrade.sh`).
- `cosign` (image signature verification — `brew install cosign` or
  `apt install cosign`).
- `python3` (interactive seed-secrets).

The GitHub Actions runners install the same toolchain via published
actions (`astral-sh/setup-uv`, `oven-sh/setup-bun`, `sigstore/cosign-installer`).

---

## Initial setup

Run once per environment. The script is **idempotent** — re-running
after a failed step picks up where it left off.

```bash
# Staging
ENV=staging ./infra/scripts/initial-setup.sh

# Prod
ENV=prod ./infra/scripts/initial-setup.sh
```

What it does, in order:

1. SSO session check against the `CertRaInstaller` permission set.
2. Operator confirmation (account / region / env / permission set).
3. Pre-deploy `IdentityStack` so the cfn-exec boundary policy exists.
4. `cdk bootstrap` with `--custom-permissions-boundary cert-ra-cfn-exec-boundary`.
5. Deploy foundation stacks in dependency order:
   - `CertRa-NetworkStack-${env}`
   - `CertRa-DataStack-${env}`
   - `CertRa-DnsStack-${env}`
   - `CertRa-SecretsStack-${env}`
   - `CertRa-ObservabilityStack-${env}`
   - `CertRa-TemporalStack-${env}` (deployed twice — see below).
6. Run `seed-secrets.py` interactively to populate the 7 SeededSecret
   shells (OAuth providers, RPC providers, session secret, Resend,
   Sentry, Anthropic API key, The Graph API key).
7. Deploy app stacks:
   - `CertRa-MigrationsStack-${env}`
   - `CertRa-AppStack-${env}`
   - `CertRa-WorkersStack-${env}`
   - `CertRa-MaintenanceStack-${env}`
8. **Re-deploy `CertRa-TemporalStack-${env}` with mTLS ON.** The
   first TemporalStack deploy runs with `CDK_TEMPORAL_MTLS_ENFORCE=false`
   so workers can bootstrap and `InitialCertIssuance` can populate
   the SeededSecret shells. The second deploy flips
   `requireClientAuth=true` and adds the cert mounts to all four
   cluster services.
9. Run the initial DB migration via `run_migration_task`.
10. **Seed the manual-metrics tables** via three one-off task invocations
    against the `cert-ra-migrate` task definition (governance, protocol,
    tokens). Each script delete-then-inserts its row subset, so re-runs
    are idempotent. This step is in `initial-setup.sh`; for a routine
    upgrade it's not repeated (manual metrics are only seeded once per
    env unless the canonical CSVs change).
11. Smoke-test `/health` against the ALB.

After step 4 there's a one-time **out-of-band** step: add the four NS
records output by `CertRa-DnsStack-${env}` to your parent zone's DNS console
for the configured per-env domain (`environments.<env>.domain` in
`deployment.config.json`). ACM cert validation blocks until delegation completes.

---

## Routine upgrade

Use for backend code, frontend assets, or schema changes that only
touch the three app stacks (AppStack / WorkersStack / MigrationsStack).

Foundation-stack changes (VPC / IAM / KMS / ACM / RDS class / Temporal
server version) require the Installer role and a manual `cdk deploy`.

### From GitHub Actions (preferred)

1. Push to `main`. The `build.yml` workflow:
   - Assumes `gha-cert-ra-build`, runs Docker buildx for linux/arm64,
     pushes to ECR at `sha-${GITHUB_SHA}`.
   - Captures the image digest.
   - In a second job, assumes `gha-cert-ra-sign` (trust policy pinned
     to this workflow at `main`) and runs `cosign sign`
     backed by KMS. The signature lives in ECR as a `.sig` sibling tag.
2. Trigger `deploy-staging.yml` from the Actions UI:
   - Paste the `sha-…` tag from the build.yml summary.
   - Optionally enable `run_migration` and `ramp_workers`.
   - Click **Run workflow**.
3. After staging validates, trigger `deploy-prod.yml`. The `prod`
   environment protection rule blocks until a reviewer approves.

### From a laptop

```bash
IMAGE_SHA=sha-abc1234 ENV=staging ./infra/scripts/upgrade.sh
IMAGE_SHA=sha-abc1234 ENV=staging RUN_MIGRATION=1 ./infra/scripts/upgrade.sh
IMAGE_SHA=sha-abc1234 ENV=prod RAMP=worker ./infra/scripts/upgrade.sh
```

`upgrade.sh`:

1. Validates the SSO session matches `CertRaUpgrader`.
2. Verifies the image exists in ECR.
3. **Verifies the image signature** via `cosign verify` against the
   public key in SSM (`/cert-ra/signing/cosign-pubkey`). Fails closed
   on any non-zero exit — an unsigned image cannot ship.
4. `cdk diff` against AppStack / WorkersStack / MigrationsStack;
   requires interactive operator confirmation.
5. (If `RUN_MIGRATION=1`) deploys MigrationsStack to the new image
   then runs the migrate task.
6. Deploys AppStack + WorkersStack:
   - AppStack registers a new task def revision; `CODE_DEPLOY`
     controller means **no rollout** until step 7.
   - WorkersStack rolls out via the ECS rolling controller with the
     circuit breaker.
7. Triggers a CodeDeploy blue/green deploy for AppStack:
   - Reads `AppTaskDefinitionArn` + CodeDeploy app/group names from
     stack outputs.
   - Builds an AppSpec referencing the new task def revision.
   - Calls `aws deploy create-deployment`.
   - `BeforeAllowTraffic` Lambda runs (cosign signature presence
     check + green TG `/health` smoke).
   - Traffic shifts: linear 10%-every-1-min in staging, canary 10% /
     5 minutes in prod.
   - `AfterAllowTraffic` Lambda runs (5xx + p99 latency check).
   - Auto-rollback alarms (5xx count, unhealthy host count) fire if
     either breaches during or after the shift.
8. (If `RAMP=worker`) walks Worker Deployments V3 10% → 50% → 100%
   per task queue, via ECS Exec into the maint container.
9. Smoke-tests `/health` against the env's public domain.

---

## Rollback

### Blue/green auto-rollback

CodeDeploy reverts automatically when:

- A `BeforeAllowTraffic` or `AfterAllowTraffic` hook returns `Failed`.
- The 5xx-count alarm or unhealthy-host alarm fires during or after
  the shift.

Operator action: none. The previous task definition stays as the
production listener's default action.

### Manual rollback

To force a roll back to a previous image:

```bash
# Re-run upgrade.sh with the last known-good sha.
IMAGE_SHA=sha-${LAST_GOOD_SHA} ENV=prod ./infra/scripts/upgrade.sh
```

Or stop the in-flight CodeDeploy deployment:

```bash
aws deploy stop-deployment --deployment-id ${DEPLOYMENT_ID} \
    --auto-rollback-enabled
```

The CodeDeploy DG's `auto_rollback` config includes
`stopped_deployment=True`, so stop-deployment triggers the same
revert as an alarm-driven rollback.

---

## Troubleshooting

### `cosign verify` fails on a freshly-built image

Likely the `sign` job in `build.yml` ran with a stale `gha-cert-ra-sign`
trust policy (e.g. after the release branch changed). Re-deploy IdentityStack (`cdk deploy CertRa-IdentityStack-${env}`)
to pick up the latest `release_branch` setting, then re-run build.yml.

### Migration task exits non-zero

```bash
aws logs tail /ecs/cert-ra-migrate --since 10m
```

The task definition family is `cert-ra-migrate`; the cluster is
`cert-ra-migrations-${env}`. Both names are stable across deploys.

### Worker can't reach Temporal frontend

Check:
- Temporal frontend NLB endpoint resolves (CertRa-TemporalStack-${env}
  output `TemporalFrontendEndpoint`).
- Worker's task has the mTLS triplet env vars
  (`TEMPORAL_TLS_CLIENT_CERT_CONTENT` / `_KEY_CONTENT` /
  `_CA_CERT_CONTENT`).
- The frontend cert hasn't expired (CertRenewal handles renewal
  daily; if disabled the cert is valid for 13 months from issuance).

### `aws ecs execute-command` into the maint container fails

The maint SG has `allow_all_outbound=False`. If the
`Maint → VPC endpoint #N` egress rules are missing, the SSM agent
inside the container can't open its control channel. Re-deploy
MaintenanceStack to re-emit the rules.

---

## References

- Secrets rotation runbook: [secrets-rotation.md](secrets-rotation.md)
- Temporal mTLS rotation runbook: [temporal-mtls-rotation.md](temporal-mtls-rotation.md)
