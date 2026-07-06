#!/usr/bin/env bash
# Tear down CertRa-AppStack-${ENV}.
#
# Provisions: ECS cluster + Fargate service (CodeDeploy controller),
# 2 ALB target groups (blue, green), 3 listeners on the public ALB
# (production :443, test :8443, HTTP redirect :80), CodeDeploy app
# + deployment group, 2 hook Lambdas (BeforeAllowTraffic /
# AfterAllowTraffic), 2 CW alarms, Route53 apex + www A-records.
#
# The listeners + ALB rules live on NetworkStack's ALB, so they
# get deleted here BEFORE NetworkStack tears down the ALB.
#
# Usage: ENV=staging ./infra/scripts/cleanup-app-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-AppStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. Drain + delete the ECS service so dependent ENIs release.
log_step "Draining ECS Fargate service"
CLUSTER_NAME="cert-ra-app-${ENV}-cluster"
SVC_NAME="cert-ra-app-${ENV}"
aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SVC_NAME" \
    --desired-count 0 >/dev/null 2>&1 || true
aws ecs delete-service --cluster "$CLUSTER_NAME" --service "$SVC_NAME" \
    --force >/dev/null 2>&1 || true

# 2. Stop any in-progress CodeDeploy deployments so the stack delete
#    can release the application + deployment group resources.
log_step "Stopping in-progress CodeDeploy deployments"
CD_APP="cert-ra-app-${ENV}"
for DEP in $(aws deploy list-deployments \
    --application-name "$CD_APP" \
    --deployment-group-name "cert-ra-app-${ENV}-dg" \
    --include-only-statuses Created Queued InProgress \
    --query 'deployments[]' --output text 2>/dev/null); do
    aws deploy stop-deployment --deployment-id "$DEP" \
        --auto-rollback-enabled 2>&1 | grep -v '^$' || true
done

# 3. Force-delete the CFN stack.
force_delete_cfn_stack "$STACK_NAME"

# 4. Stranded log groups.
cleanup_log_group "/ecs/cert-ra-app-${ENV}"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECS service" aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SVC_NAME" --query 'services[?status==`ACTIVE`]'
verify_resource_gone "CodeDeploy app" aws deploy get-application --application-name "$CD_APP"

log_header "$STACK_NAME cleanup complete"
