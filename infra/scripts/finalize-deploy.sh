#!/usr/bin/env bash
# Final deploy sequence after apply-pending-fixes.sh / finish-pending-fixes.sh
# has landed every CDK stack but the app is still serving from the OLD
# task-definition revision. Runs three things in order:
#
#   1. Schema migration (alembic upgrade head). Pending migrations would
#      cause the new app + worker tasks to crash on `relation X does not
#      exist` the first time they touch a new table — must precede the
#      traffic shift.
#   2. CodeDeploy blue/green for AppStack. CDK only registered the new
#      AppStack task-def revision; CodeDeploy is what actually shifts
#      production traffic onto it.
#   3. verify-deploy.sh — full health gate (every stack in a healthy
#      terminal state, every ECS service running == desired, /landing/
#      returns 2xx).
#
# Use this after a recovery deploy when you're past the foundation
# stacks and just need to finalise the traffic shift. Not a substitute
# for upgrade.sh on a routine image bump (that handles migrations +
# CodeDeploy itself).
#
# Required:
#   ENV — `staging` or `prod`
#
# Optional:
#   SKIP_MIGRATION=1 — skip the schema migration step (set only if you
#                       know the schema is already current).
#   SKIP_VERIFY=1    — skip the final verify-deploy.sh call (set only
#                       if you intend to run verify-deploy manually).

set -euo pipefail

ENV="${ENV:?ENV is required (staging or prod)}"
PROFILE="cert-ra-${ENV}-installer"
EXPECTED_PERMISSION_SET="CertRaInstaller"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$(resolve_region "$ENV")"

log_header "Finalising cert-ra-${ENV} deploy"

require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo
echo "About to finalise the deploy:"
echo "    Account:       $ACCOUNT_ID"
echo "    Region:        $AWS_REGION"
echo "    Environment:   $ENV"
echo "    Steps:"
echo "      1. Schema migration  ${SKIP_MIGRATION:-0:+(SKIPPED)}"
echo "      2. CodeDeploy blue/green for AppStack"
echo "      3. verify-deploy.sh  ${SKIP_VERIFY:-0:+(SKIPPED)}"
echo
read -r -p "Continue? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

# --- Step 1: schema migration ------------------------------------------------
# Alembic upgrades land additive schema changes (new tables, new columns
# with defaults). Forward-compatible — the OLD task def keeps reading
# the OLD shape during the bake; the NEW task def is the only one that
# needs the new tables. Running migration *before* the traffic shift
# means the new revision finds the schema ready when CodeDeploy starts
# its canary.

if [[ "${SKIP_MIGRATION:-0}" == "1" ]]; then
    log_step "SKIPPING schema migration (SKIP_MIGRATION=1)"
    echo "Reminder: any new tables/columns the new image expects MUST"
    echo "exist in RDS before traffic shifts, or workers + app will"
    echo "crash with 'relation X does not exist' on the first activity."
else
    log_step "Running schema migration (alembic upgrade head)"
    run_migration_task
fi

# --- Step 2: CodeDeploy blue/green ------------------------------------------
# The AppStack ECS service is `deployment_controller=CODE_DEPLOY`, so a
# fresh `cdk deploy CertRa-AppStack-${ENV}` only registers the new task
# definition revision — no traffic shift. CodeDeploy is what actually
# routes prod onto the new task def via the canary config in
# AppStack's BlueGreenDeployment construct (10% / 5 min + bake in
# prod; linear 10%/min in staging).

log_step "Triggering CodeDeploy blue/green for AppStack"

APP_TASK_DEF_ARN=$(stack_output "CertRa-AppStack-${ENV}" "AppTaskDefinitionArn")
CD_APP=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployApplicationName")
CD_DG=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployDeploymentGroupName")

# The AppSpec must specify the container name + port the load balancer
# routes to. LitestarService names the container "App" and listens on
# 8000 (DEFAULT_CONTAINER_PORT). If those values change in the
# construct, this AppSpec needs to change too.
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
echo "(staging = linear 10%/min ~10 min; prod = canary 10% + bake ~15-20 min)"

# `aws deploy wait` polls every 15s. Returns non-zero on rollback /
# failure. The BeforeAllowTraffic Lambda + AfterAllowTraffic Lambda
# both run during this wait; any auto-rollback alarm trips here too.
if ! aws deploy wait deployment-successful --deployment-id "$DEPLOYMENT_ID"; then
    echo
    echo "FATAL: blue/green deploy failed or rolled back." >&2
    echo "Inspect: aws deploy get-deployment --deployment-id $DEPLOYMENT_ID" >&2
    echo "Logs:    aws logs tail /ecs/cert-ra-app-${ENV} --since 30m --format short" >&2
    exit 1
fi

# --- Step 3: verify-deploy ---------------------------------------------------
# Final go/no-go. Confirms every stack in a healthy terminal state,
# every ECS service runningCount == desiredCount, and /landing/ returns
# 2xx. Without this final check, a CodeDeploy success doesn't
# guarantee everything else is healthy (e.g., a worker crash-looping
# silently doesn't show up in CodeDeploy output).

if [[ "${SKIP_VERIFY:-0}" == "1" ]]; then
    log_step "SKIPPING verify-deploy.sh (SKIP_VERIFY=1)"
    echo "Run it manually before considering the deploy shipped:"
    echo "    ENV=$ENV ./verify-deploy.sh"
else
    log_step "Running verify-deploy.sh"
    # verify-deploy.sh sets its own AWS_PROFILE/AWS_REGION but they
    # match what we've already exported, so this is a no-op overlay.
    if ! "$SCRIPT_DIR/verify-deploy.sh"; then
        echo
        echo "FATAL: verify-deploy.sh reported problems — see output above." >&2
        echo "The deploy may be partially shipped; investigate before declaring done." >&2
        exit 1
    fi
fi

# --- Done --------------------------------------------------------------------

DOMAIN=$(stack_output "CertRa-DnsStack-${ENV}" "DomainName")
log_header "Deploy finalised"
echo "App:     https://${DOMAIN}"
echo
echo "Recommended manual smoke tests:"
echo "  1. Log in to the app with an existing account"
echo "     — exercises CSRF allowlist + Secure cookies + auth flow."
echo "  2. Update your profile picture in the UI"
echo "     — exercises S3 SSE-KMS write + read end-to-end."
echo "  3. Tail worker logs for ~2 min to confirm no SQL/Temporal errors:"
echo "     aws logs tail /ecs/cert-ra-worker-metrics-${ENV} --since 5m --follow"
echo "     aws logs tail /ecs/cert-ra-worker-alerts-${ENV}  --since 5m --follow"
