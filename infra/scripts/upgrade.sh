#!/usr/bin/env bash
# Routine upgrade of cert-ra. Backend, frontend, or schema changes only.
#
# For infrastructure changes (VPC, IAM, KMS, ACM, Temporal server upgrade,
# RDS class change), use initial-setup.sh or a manual `cdk deploy` under
# the Installer permission set. The CertRaUpgrader permission set this
# script assumes does NOT have permission to touch foundation stacks.
#
# Usage:
#   IMAGE_SHA=sha-abc1234 ENV=prod ./upgrade.sh                  # runs migration
#   IMAGE_SHA=sha-abc1234 ENV=prod SKIP_MIGRATION=1 ./upgrade.sh # skips schema mig
#   IMAGE_SHA=sha-abc1234 ENV=prod SKIP_PREFLIGHT_VERIFY=1 \     # bypass pre-flight
#                                  ./upgrade.sh                  #   health check
#   IMAGE_SHA=sha-abc1234 ENV=prod SKIP_VERIFY=1 ./upgrade.sh    # skip post-deploy
#                                                                #   verify
#   IMAGE_SHA=sha-abc1234 ENV=prod RAMP=worker ./upgrade.sh
#
# Notes:
#   - IMAGE_SHA must already exist in ECR (built by build.yml).
#   - The cosign signature for the image is verified before any deploy.
#     Without that, a compromised Upgrader could push a malicious image
#     and ship it.
#   - The schema migration runs by default. Alembic upgrades are
#     forward-compatible (we follow the add-column-copy-drop pattern for
#     renames), and skipping migrations historically caused silent drift
#     between the deployed image's expected schema and what RDS actually
#     held — the workers would start hitting `relation X does not exist`
#     errors on the first activity that touched the new tables. Set
#     SKIP_MIGRATION=1 only when you know a specific migration in this
#     batch is breaking and you want to handle it under a maintenance
#     window instead.

set -euo pipefail

ENV="${ENV:-staging}"
IMAGE_SHA="${IMAGE_SHA:?IMAGE_SHA required (e.g. sha-abc1234)}"
PROFILE="cert-ra-${ENV}-upgrader"
EXPECTED_PERMISSION_SET="CertRaUpgrader"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

# Per-env CDK output directory so concurrent staging + prod runs
# don't collide on cdk.out. See the matching comment in
# initial-setup.sh for the why.
CDK_OUT_DIR="cdk.out.${ENV}"
cdk_run() {
    bunx cdk --output "$CDK_OUT_DIR" "$@"
}

source "$SCRIPT_DIR/_common.sh"

# Region from env_config.region (_config.py), same as the CDK app,
# unless AWS_REGION overrides. See resolve_region in _common.sh.
REGION="$(resolve_region "$ENV")"

log_header "Upgrading cert-ra-${ENV} → ${IMAGE_SHA}"

require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

cd "$INFRA_DIR"
uv sync --frozen

# lending-markets-rating Node deps must be present on disk before
# the Docker asset build kicks off. See the matching block in
# initial-setup.sh for the why.
REPO_ROOT="$(dirname "$INFRA_DIR")"
if [[ -d "$REPO_ROOT/lending-markets-rating" ]]; then
    log_step "Installing lending-markets-rating Node dependencies"
    # flock so concurrent staging + prod upgrades don't race on the
    # shared node_modules directory.
    (
        flock -x 9
        cd "$REPO_ROOT/lending-markets-rating" && npm install
    ) 9>/tmp/cert-ra-lending-markets-npm.lock
fi

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export CDK_ENV="$ENV"
# CDK reads this when registering the new task definition revision in
# AppStack / WorkersStack / MigrationsStack. See `infra/app.py`'s
# `os.environ.get("CDK_APP_IMAGE_TAG", "latest")` lookup.
export CDK_APP_IMAGE_TAG="$IMAGE_SHA"

ECR_REPO="cert-ra-${ENV}"
COSIGN_PUBKEY_PARAM="/cert-ra/${ENV}/signing/cosign-pubkey"

# Step 1: verify the image exists in ECR before doing anything else.
log_step "Verifying image ${ECR_REPO}:${IMAGE_SHA} exists in ECR"
if ! aws ecr describe-images \
    --repository-name "$ECR_REPO" \
    --image-ids "imageTag=${IMAGE_SHA}" >/dev/null 2>&1; then
    echo "Image ${ECR_REPO}:${IMAGE_SHA} not found in ECR. Build + push it first."
    exit 1
fi

# Step 1.5: H1 — verify the image is signed by gha-cert-ra-sign-${ENV}.
# Without this, a compromised Upgrader could push a malicious image and
# ship it. Fails closed: any non-zero exit aborts before any traffic
# shift can happen.
log_step "Verifying image signature (cosign)"
DIGEST=$(aws ecr describe-images \
    --repository-name "$ECR_REPO" \
    --image-ids "imageTag=${IMAGE_SHA}" \
    --query 'imageDetails[0].imageDigest' --output text)
ECR_URI=$(aws ecr describe-repositories \
    --repository-names "$ECR_REPO" \
    --query 'repositories[0].repositoryUri' --output text)
aws ssm get-parameter --name "$COSIGN_PUBKEY_PARAM" \
    --query 'Parameter.Value' --output text > /tmp/cert-ra-cosign.pub
trap 'rm -f /tmp/cert-ra-cosign.pub' EXIT
# Pass a fresh ECR auth token to cosign directly (rather than relying on
# the operator's ~/.docker/config.json, which silently expires after 12h
# and breaks the verify with "Your authorization token has expired").
# The operator's AWS profile already has ecr:GetAuthorizationToken (it's
# what aws ecr describe-images above relies on).
ECR_PASSWORD=$(aws ecr get-login-password)
if ! cosign verify \
    --key /tmp/cert-ra-cosign.pub \
    --insecure-ignore-tlog \
    --registry-username AWS \
    --registry-password "$ECR_PASSWORD" \
    "${ECR_URI}@${DIGEST}" >/dev/null; then
    echo "FATAL: image ${IMAGE_SHA} (${DIGEST}) is not signed by gha-cert-ra-sign-${ENV} — aborting"
    exit 1
fi
echo "Signature verified for ${DIGEST}"

# Step 1.7: pre-flight verify. Catches the case where a foundation
# stack the Upgrader can't touch is already broken (e.g., a stack in
# ROLLBACK_COMPLETE from a previous failed initial-setup), so we don't
# walk into it and make things worse. If something the Upgrader can't
# fix is wrong, the right move is to abort and have an operator with
# the Installer permission set run apply-pending-fixes.sh first. Set
# SKIP_PREFLIGHT_VERIFY=1 only if you're knowingly upgrading through a
# pre-existing issue.
if [[ "${SKIP_PREFLIGHT_VERIFY:-0}" == "1" ]]; then
    log_step "SKIPPING pre-flight verify-deploy (SKIP_PREFLIGHT_VERIFY=1)"
else
    log_step "Pre-flight: verifying environment is healthy before upgrade"
    if ! "$SCRIPT_DIR/verify-deploy.sh"; then
        echo "FATAL: pre-flight verify failed — see output above." >&2
        echo "Fix the reported problems before upgrading. If they require" >&2
        echo "touching a foundation stack the Upgrader can't reach, run" >&2
        echo "apply-pending-fixes.sh as the Installer first. To upgrade" >&2
        echo "anyway (not recommended), set SKIP_PREFLIGHT_VERIFY=1." >&2
        exit 1
    fi
fi

# Step 2: diff before deploying. Fails the script if non-trivial drift
# exists. CertRaUpgrader's IAM lets us touch only AppStack, WorkersStack,
# and MigrationsStack — those are the only stacks we diff.
log_step "cdk diff (review changes before applying)"
cdk_run diff \
    "CertRa-AppStack-${ENV}" \
    "CertRa-WorkersStack-${ENV}" \
    "CertRa-MigrationsStack-${ENV}"

read -r -p "Proceed with deploy? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

# Step 3: deploy MigrationsStack to the new image tag. Required for both
# the optional schema migration and the always-on manual-metrics seed
# below — both run on the cert-ra-migrate task definition, so it must
# already point at the new image (and its packaged protocol JSON). The
# stack registers a new task-definition revision only; there's no service
# and no traffic, so this is cheap and idempotent even with no migration.
log_step "Updating MigrationsStack to new image tag"
cdk_run deploy "CertRa-MigrationsStack-${ENV}" --require-approval=never

# Step 3a: schema migration BEFORE service update. Forward-compatible
# migrations (new column with default; rename via add-column-copy-drop
# pattern; etc.) are the safe order — the new app revision is the only
# one that needs the new schema, but the OLD revision keeps running on
# the OLD schema during the canary, so we land schema first to avoid
# the CodeDeploy traffic shift racing the workers. For breaking
# migrations the operator runs them under a maintenance window
# instead — set SKIP_MIGRATION=1 to defer.
if [[ "${SKIP_MIGRATION:-0}" == "1" ]]; then
    log_step "SKIPPING schema migration (SKIP_MIGRATION=1)"
    echo "Reminder: any new tables/columns the new image expects MUST be"
    echo "applied separately before traffic shifts, or workers + app will"
    echo "crash with 'relation X does not exist'."
else
    log_step "Running schema migration (set SKIP_MIGRATION=1 to defer)"
    run_migration_task
fi

# Step 3b: seed manual metrics from the packaged payloads.
# Runs on every upgrade — the payloads shipped in the image are the source
# of truth and replace manual_metric rows per protocol and per token.
# Ordered after the migration so any new schema is in place before the
# seed writes.
run_seed_script "protocol" certora-risk-seed-metrics
run_seed_script "token-metrics" certora-risk-seed-token-metrics

# Governance metrics use seed-once semantics: the seeder is guarded and
# no-ops if any GOVERNANCE row already exists, so operators keep owning
# them in the UI. We invoke it here (without --force) so an environment
# that was never seeded — e.g. one stood up before governance seeding
# existed — gets backfilled on its next upgrade. Once rows are present
# this is a harmless no-op; it deliberately does NOT push later CSV edits
# over operator UI changes (that would require --force, which the deploy
# scripts never pass).
run_seed_script "governance" certora-risk-seed-governance

# Step 4a: deploy app stacks. AppStack registers a new task definition
# revision but does NOT shift traffic (CodeDeploy controller — see
# LitestarService.deployment_controller=CODE_DEPLOY). WorkersStack
# rolls out via the ECS rolling controller with the circuit breaker.
log_step "Deploying AppStack + WorkersStack"
cdk_run deploy \
    "CertRa-AppStack-${ENV}" \
    "CertRa-WorkersStack-${ENV}" \
    --require-approval=never \
    --rollback=true

# Step 4b: blue/green traffic shift for AppStack via CodeDeploy.
# Read the task def ARN we just registered + the CodeDeploy app /
# deployment group names from the AppStack outputs, then build an
# AppSpec referencing the new task def revision and create the
# CodeDeploy deployment.
log_step "Triggering CodeDeploy blue/green for AppStack"
APP_TASK_DEF_ARN=$(stack_output "CertRa-AppStack-${ENV}" "AppTaskDefinitionArn")
CD_APP=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployApplicationName")
CD_DG=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployDeploymentGroupName")

# The AppSpec must specify the container name + port that the load
# balancer routes to. LitestarService names the container "App" and
# listens on 8000 (the DEFAULT_CONTAINER_PORT in the construct).
APPSPEC=$(jq -n --arg td "$APP_TASK_DEF_ARN" '{
    version: "0.0",
    Resources: [{
        TargetService: {
            Type: "AWS::ECS::Service",
            Properties: {
                TaskDefinition: $td,
                LoadBalancerInfo: { ContainerName: "App", ContainerPort: 8000 }
            }
        }
    }]
}')
DEPLOYMENT_ID=$(aws deploy create-deployment \
    --application-name "$CD_APP" \
    --deployment-group-name "$CD_DG" \
    --revision "revisionType=AppSpecContent,appSpecContent={content='${APPSPEC}'}" \
    --query 'deploymentId' --output text)
echo "CodeDeploy deployment: $DEPLOYMENT_ID"

# Wait for the deploy to either fully shift traffic or roll back. The
# BeforeAllowTraffic + AfterAllowTraffic hooks run inside this wait;
# auto-rollback alarms fire here too.
if ! aws deploy wait deployment-successful --deployment-id "$DEPLOYMENT_ID"; then
    echo "Blue/green deploy failed or rolled back — inspect CodeDeploy console:"
    echo "  aws deploy get-deployment --deployment-id $DEPLOYMENT_ID"
    exit 1
fi

# Step 5: optional worker version ramp. Temporal SDK 1.26+'s Worker
# Deployments V3 lets us ramp traffic between worker build IDs
# gradually. After M5 (Temporal mTLS), `temporal` commands need a
# client cert. The operator's laptop doesn't have one — we run the
# commands inside the maintenance container (which mounts the maint
# mTLS cert) via ECS Exec.
if [[ "${RAMP:-}" == "worker" ]]; then
    log_step "Ramping Worker Deployment Version (via maintenance container)"
    MAINT_CLUSTER=$(stack_output "CertRa-MaintenanceStack-${ENV}" "ClusterName")
    MAINT_SERVICE=$(stack_output "CertRa-MaintenanceStack-${ENV}" "ServiceName")
    MAINT_TASK=$(aws ecs list-tasks \
        --cluster "$MAINT_CLUSTER" \
        --service-name "$MAINT_SERVICE" \
        --query 'taskArns[0]' --output text)
    if [[ -z "$MAINT_TASK" || "$MAINT_TASK" == "None" ]]; then
        echo "No running maint task in $MAINT_CLUSTER — skipping ramp"
        exit 1
    fi

    run_in_maint() {
        # ECS Exec into the maint container and run a temporal command.
        # The container's /usr/local/bin/temporal wrapper (when present)
        # applies the cert flags; until that wrapper lands operators
        # run via the Python SDK from inside the container's source
        # tree. Either way the cert is mounted via env vars.
        aws ecs execute-command \
            --cluster "$MAINT_CLUSTER" --task "$MAINT_TASK" \
            --container Maint --interactive --command "$1"
    }
    for deployment in cert-ra-metrics cert-ra-alerts; do
        for pct in 10 50 100; do
            echo "  $deployment → $pct%"
            run_in_maint "temporal worker-deployment set-ramping-version \
                --deployment-name $deployment \
                --build-id $IMAGE_SHA \
                --percentage $pct"
            sleep 300  # 5 min between ramp steps; tune to traffic
        done
        run_in_maint "temporal worker-deployment set-current-version \
            --deployment-name $deployment \
            --build-id $IMAGE_SHA"
    done
fi

# Step 6: smoke test against the public hostname (Route53 alias →
# ALB → ECS). This is the fast first signal — /landing/ returns 200
# the moment Litestar is up.
log_step "Smoke test"
DOMAIN=$(stack_output "CertRa-DnsStack-${ENV}" "DomainName")
if curl --fail --silent --show-error "https://${DOMAIN}/landing/" >/dev/null; then
    echo "OK"
else
    echo "Smoke test failed — check ECS service events:"
    echo "  aws ecs describe-services --cluster cert-ra-app-${ENV}-cluster \\"
    echo "      --services cert-ra-app-${ENV}"
    exit 1
fi

# Step 7: post-deploy verify. /landing/ passing doesn't mean the upgrade
# was successful end-to-end — workers can be crash-looping on
# something unrelated to the public path (missing schema, Temporal
# auth, etc.) while the public ALB serves OK. verify-deploy is the
# comprehensive gate: every stack healthy, every ECS service running
# == desired, /landing/ still passing. Set SKIP_VERIFY=1 to defer to a
# manual verify-deploy call.
if [[ "${SKIP_VERIFY:-0}" == "1" ]]; then
    log_step "SKIPPING post-deploy verify (SKIP_VERIFY=1)"
    echo "Run verify-deploy.sh manually before declaring done:"
    echo "    ENV=$ENV ./verify-deploy.sh"
else
    log_step "Post-deploy: verifying every stack + service is healthy"
    if ! "$SCRIPT_DIR/verify-deploy.sh"; then
        echo "FATAL: post-deploy verify failed — see output above." >&2
        echo "The image has shipped but the environment is not fully" >&2
        echo "healthy. Inspect logs of any flagged service:" >&2
        echo "    aws logs tail /ecs/<service-name> --since 30m --format short" >&2
        exit 1
    fi
fi

log_header "Upgrade complete"
echo "URL: https://${DOMAIN}"
echo "Image: ${ECR_REPO}:${IMAGE_SHA} (${DIGEST})"
