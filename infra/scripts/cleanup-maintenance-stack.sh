#!/usr/bin/env bash
# Tear down CertRa-MaintenanceStack-${ENV}: dedicated cert-ra-maint-${ENV}
# ECS cluster + always-on Fargate service.
#
# Usage: ENV=staging ./infra/scripts/cleanup-maintenance-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-MaintenanceStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

CLUSTER_NAME="cert-ra-maint-${ENV}"
SVC_NAME="cert-ra-maint-${ENV}"

log_step "Draining maintenance service"
aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SVC_NAME" \
    --desired-count 0 >/dev/null 2>&1 || true
aws ecs delete-service --cluster "$CLUSTER_NAME" --service "$SVC_NAME" \
    --force >/dev/null 2>&1 || true

force_delete_cfn_stack "$STACK_NAME"

cleanup_log_group "/ecs/cert-ra-maint-${ENV}"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECS cluster" aws ecs describe-clusters --clusters "$CLUSTER_NAME" --query 'clusters[?status==`ACTIVE`]'

log_header "$STACK_NAME cleanup complete"
