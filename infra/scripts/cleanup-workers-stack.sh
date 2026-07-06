#!/usr/bin/env bash
# Tear down CertRa-WorkersStack-${ENV}: ECS cluster + 2 Fargate services
# (metrics + alerts).
#
# Usage: ENV=staging ./infra/scripts/cleanup-workers-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-WorkersStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. Drain + delete both worker services.
CLUSTER_NAME="cert-ra-workers-${ENV}"
for SVC in "cert-ra-worker-metrics-${ENV}" "cert-ra-worker-alerts-${ENV}"; do
    log_step "Draining + deleting $SVC"
    aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SVC" \
        --desired-count 0 >/dev/null 2>&1 || true
    aws ecs delete-service --cluster "$CLUSTER_NAME" --service "$SVC" \
        --force >/dev/null 2>&1 || true
done

# 2. Force-delete the CFN stack.
force_delete_cfn_stack "$STACK_NAME"

# 3. Stranded log groups.
for LG in "/ecs/cert-ra-worker-metrics-${ENV}" "/ecs/cert-ra-worker-alerts-${ENV}"; do
    cleanup_log_group "$LG"
done

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECS cluster" aws ecs describe-clusters --clusters "$CLUSTER_NAME" --query 'clusters[?status==`ACTIVE`]'

log_header "$STACK_NAME cleanup complete"
