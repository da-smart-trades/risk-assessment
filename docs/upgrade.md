# cert-ra Upgrade Runbook

Covers routine image upgrades (backend, frontend, schema changes) via
`infra/scripts/upgrade.sh`. For infrastructure changes (VPC, IAM, KMS, ACM,
RDS class, Temporal server) use `initial-setup.sh` or a manual `cdk deploy`
under the **Installer** permission set — `upgrade.sh` uses the more-restricted
**Upgrader** permission set which cannot touch foundation stacks.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| AWS SSO profile | `cert-ra-{ENV}-upgrader` configured with the `CertRaUpgrader` permission set |
| Tools on PATH | `aws`, `jq`, `bunx` (CDK via bun), `cosign`, `uv` |
| Image in ECR | The `IMAGE_SHA` tag must already exist, built and pushed by `build.yml` |
| Signed image | The image must be signed by `gha-cert-ra-sign-{ENV}` — the script verifies this and hard-aborts if the signature is missing |

---

## Invocation

```bash
# Standard upgrade — runs migration, shifts traffic, full health verification
IMAGE_SHA=sha-abc1234 ENV=prod ./infra/scripts/upgrade.sh

# Staging
IMAGE_SHA=sha-abc1234 ENV=staging ./infra/scripts/upgrade.sh
```

`IMAGE_SHA` is the ECR image tag emitted by the `build.yml` GitHub Actions
workflow (e.g. `sha-abc1234`).

### Optional flags

| Flag | When to use |
|------|-------------|
| `SKIP_MIGRATION=1` | A specific migration in this batch is known-breaking and will be handled under a maintenance window instead. **Warning:** any tables/columns the new image expects must already exist, or workers will crash with `relation X does not exist`. |
| `SKIP_PREFLIGHT_VERIFY=1` | Upgrading through a known pre-existing issue. Not recommended — fix the issue first, or run `apply-pending-fixes.sh` as the Installer if it requires a foundation stack. |
| `SKIP_VERIFY=1` | Defer the post-deploy health gate to a manual `verify-deploy.sh` call. |
| `RAMP=worker` | Also ramp Temporal Worker Deployment versions after the app traffic shift (see [Worker ramp](#worker-ramp) below). |

```bash
# Skip schema migration
IMAGE_SHA=sha-abc1234 ENV=prod SKIP_MIGRATION=1 ./infra/scripts/upgrade.sh

# Bypass pre-flight health check (known pre-existing issue)
IMAGE_SHA=sha-abc1234 ENV=prod SKIP_PREFLIGHT_VERIFY=1 ./infra/scripts/upgrade.sh

# Skip post-deploy verification
IMAGE_SHA=sha-abc1234 ENV=prod SKIP_VERIFY=1 ./infra/scripts/upgrade.sh

# Upgrade + ramp Temporal workers
IMAGE_SHA=sha-abc1234 ENV=prod RAMP=worker ./infra/scripts/upgrade.sh
```

---

## What the script does

### Step 1 — ECR image existence check

Verifies `ECR:{ECR_REPO}:{IMAGE_SHA}` exists before doing anything else. Fails
immediately if the image isn't found — build and push it first.

### Step 1.5 — Cosign signature verification

Fetches the cosign public key from SSM Parameter Store
(`/cert-ra/{ENV}/signing/cosign-pubkey`) and verifies the image digest is
signed by `gha-cert-ra-sign-{ENV}`. Hard-aborts on failure. This prevents a
compromised Upgrader role from shipping an unsigned or tampered image.

### Step 1.7 — Pre-flight health check

Runs `verify-deploy.sh` against the **current** environment before touching
anything. Catches broken foundation stacks early so the upgrade doesn't walk
into a degraded environment and make things harder to recover.

If pre-flight fails, the right path is:
1. Identify the broken stack from the output.
2. If it requires the Installer permission set (foundation stack), run
   `apply-pending-fixes.sh` first.
3. Re-run `upgrade.sh` once the environment is healthy.

Set `SKIP_PREFLIGHT_VERIFY=1` only if you are knowingly upgrading through a
pre-existing, non-blocking issue.

### Step 2 — CDK diff + confirmation prompt

Runs `cdk diff` against `CertRa-AppStack-{ENV}`, `CertRa-WorkersStack-{ENV}`,
and `CertRa-MigrationsStack-{ENV}`. **Pauses for explicit `y` confirmation**
before applying any changes.

### Step 3 — MigrationsStack update

Deploys `CertRa-MigrationsStack-{ENV}` to register the new task definition
revision. There is no service and no traffic; this is cheap and idempotent.
Required so that both the schema migration task and the protocol-metrics seed
task run from the new image.

### Step 3a — Alembic schema migration

Runs `alembic upgrade head` as a Fargate one-off task on the MigrationsStack
cluster. Schema lands **before** the traffic shift so the new app revision
finds the expected tables/columns when CodeDeploy starts its canary.

Migrations follow the forward-compatible add-column-copy-drop pattern: the old
revision keeps running against the old schema during the canary bake, while the
new revision is the only consumer of new schema objects.

Skip with `SKIP_MIGRATION=1` only when deferring to a maintenance window.

### Step 3b — Protocol metrics seed

Runs `certora-risk-seed-metrics` as a Fargate task using the same
MigrationsStack task definition with a command override. Rewrites all
`manual_metric` rows per protocol from the JSON payloads bundled in the new
image. Runs on every upgrade — the packaged JSON is the source of truth.

Ordered after the migration so any new schema is in place before the seed
writes.

### Step 4a — AppStack + WorkersStack deploy

Deploys both stacks:

- **AppStack**: registers a new ECS task definition revision. Does **not** shift
  traffic — the service uses `deployment_controller=CODE_DEPLOY`, so CodeDeploy
  owns the ALB shift (Step 4b).
- **WorkersStack**: rolling update with the ECS circuit breaker. Workers start
  draining Temporal task queues on the new build ID immediately.

### Step 4b — CodeDeploy blue/green traffic shift

Creates a CodeDeploy deployment pointing the ALB at the new AppStack task
definition revision. The script waits for `deployment-successful`:

| Environment | Strategy |
|-------------|----------|
| staging | Linear 10%/min (~10 min total) |
| prod | Canary 10% + bake period (~15-20 min total) |

BeforeAllowTraffic and AfterAllowTraffic Lambda hooks run during this wait.
Auto-rollback alarms fire here if the deployment is unhealthy.

If the deployment fails or rolls back:

```bash
aws deploy get-deployment --deployment-id <DEPLOYMENT_ID>
aws logs tail /ecs/cert-ra-app-${ENV} --since 30m --format short
```

### Step 5 — Worker ramp (optional, `RAMP=worker`) {#worker-ramp}

Uses Temporal SDK 1.26+ Worker Deployments V3 to gradually ramp traffic between
worker build IDs. Runs `temporal worker-deployment set-ramping-version` at 10%,
50%, then 100% (5-minute waits between steps) via ECS Exec into the maintenance
container (which holds the mTLS client cert). Then promotes to current with
`set-current-version`.

Applies to both `cert-ra-metrics` and `cert-ra-alerts` deployment names.

Only needed after changes that alter Temporal workflow or activity interfaces.

### Step 6 — Smoke test

```
curl https://{domain}/landing/
```

First signal that the new task is serving traffic. Confirms the ALB target is
healthy and Litestar is up.

### Step 7 — Post-deploy verification

Runs `verify-deploy.sh` as a final gate. A successful CodeDeploy deployment
does not guarantee everything is healthy — workers can be crash-looping on
unrelated issues (missing schema, Temporal auth misconfiguration) without
affecting the ALB path. `verify-deploy.sh` checks:

1. All 11 CloudFormation stacks in a healthy terminal state
2. All ECS services have `runningCount == desiredCount`
3. `https://{domain}/landing/` returns 200

---

## verify-deploy.sh — standalone health check

Safe to run at any time (read-only AWS API calls).

```bash
ENV=prod    ./infra/scripts/verify-deploy.sh
ENV=staging ./infra/scripts/verify-deploy.sh
```

**Stacks checked:**

- CertRa-IdentityStack
- CertRa-NetworkStack
- CertRa-DataStack
- CertRa-DnsStack
- CertRa-SecretsStack
- CertRa-ObservabilityStack
- CertRa-TemporalStack
- CertRa-MigrationsStack
- CertRa-AppStack
- CertRa-WorkersStack
- CertRa-MaintenanceStack

**ECS clusters checked:** app, workers, maintenance, temporal, migrations.

Healthy terminal states: `CREATE_COMPLETE`, `UPDATE_COMPLETE`,
`UPDATE_COMPLETE_CLEANUP_IN_PROGRESS`.

---

## finalize-deploy.sh — recovery path

Used **after** `apply-pending-fixes.sh` or `finish-pending-fixes.sh` has landed
every CDK stack but the ALB is still serving the old task definition revision.
Requires the **Installer** permission set (not Upgrader).

Runs: migration → CodeDeploy blue/green → verify-deploy.

```bash
ENV=prod    ./infra/scripts/finalize-deploy.sh
ENV=staging ./infra/scripts/finalize-deploy.sh

# Skip migration if schema is already current
SKIP_MIGRATION=1 ENV=prod ./infra/scripts/finalize-deploy.sh
```

This is **not** a substitute for `upgrade.sh` on a routine image bump.

---

## Post-deploy manual smoke tests

After `upgrade.sh` reports success, recommended manual checks:

1. **Log in** with an existing account — exercises CSRF allowlist, Secure
   cookies, and the full auth flow.
2. **Update profile picture** in the UI — exercises S3 SSE-KMS write + read
   end-to-end.
3. **Tail worker logs** for ~2 minutes to confirm no SQL or Temporal errors:

```bash
aws logs tail /ecs/cert-ra-worker-metrics-${ENV} --since 5m --follow
aws logs tail /ecs/cert-ra-worker-alerts-${ENV}  --since 5m --follow
```

---

## Failure recovery quick-reference

| Symptom | Action |
|---------|--------|
| Pre-flight verify fails | Fix the broken stack first. If it's a foundation stack (requires Installer), run `apply-pending-fixes.sh` first. Then re-run `upgrade.sh`. |
| `Image not found in ECR` | Trigger the `build.yml` workflow and wait for it to push the image. |
| `image is not signed` | The image was not built by the trusted GHA workflow. Do not bypass — investigate the build pipeline. |
| CDK diff shows unexpected changes | Review carefully; the Upgrader role can only touch AppStack, WorkersStack, and MigrationsStack. |
| CodeDeploy deployment fails/rolls back | `aws deploy get-deployment --deployment-id <id>` + check app logs (see below). |
| Stack in `ROLLBACK_COMPLETE` | The stack must be deleted before it can be recreated: `aws cloudformation delete-stack --stack-name <name>` (requires Installer). |
| Stack in `*_ROLLBACK_IN_PROGRESS` | Wait for it to settle, then re-run `verify-deploy.sh`. |
| ECS service unhealthy (`running != desired`) | Check service logs: `aws logs tail /ecs/<service-name> --since 30m --format short` |
| Migration task failed | `aws logs tail /ecs/cert-ra-migrate --since 5m` |
| Workers crash-looping after deploy | Likely missing schema — check if `SKIP_MIGRATION=1` was set without applying migrations separately. |
| `/landing/` smoke test fails | Check ALB target health + app logs: `aws logs tail /ecs/cert-ra-app-${ENV} --since 30m --format short` |

### Common log commands

```bash
# App service
aws logs tail /ecs/cert-ra-app-${ENV} --since 30m --format short

# Metrics worker
aws logs tail /ecs/cert-ra-worker-metrics-${ENV} --since 30m --format short

# Alerts worker
aws logs tail /ecs/cert-ra-worker-alerts-${ENV} --since 30m --format short

# Migration task
aws logs tail /ecs/cert-ra-migrate --since 5m

# ECS service events
aws ecs describe-services \
    --cluster cert-ra-app-${ENV}-cluster \
    --services cert-ra-app-${ENV}
```
