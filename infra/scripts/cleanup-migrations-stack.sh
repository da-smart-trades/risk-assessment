#!/usr/bin/env bash
# Tear down CertRa-MigrationsStack-${ENV}: ECS cluster + one task
# definition (no service). Simplest of the cleanup scripts — nothing
# special to drain because there's no long-running service.
#
# Usage: ENV=staging ./infra/scripts/cleanup-migrations-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-MigrationsStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# Stop any in-progress task on the migrate task family.
CLUSTER_NAME="cert-ra-migrations-${ENV}"
log_step "Stopping in-progress migrate tasks"
for TASK in $(aws ecs list-tasks --cluster "$CLUSTER_NAME" \
    --family cert-ra-migrate \
    --query 'taskArns[]' --output text 2>/dev/null); do
    aws ecs stop-task --cluster "$CLUSTER_NAME" --task "$TASK" \
        --reason "cleanup-migrations-stack.sh" >/dev/null 2>&1 || true
done

force_delete_cfn_stack "$STACK_NAME"

cleanup_log_group "/ecs/cert-ra-migrate"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECS cluster" aws ecs describe-clusters --clusters "$CLUSTER_NAME" --query 'clusters[?status==`ACTIVE`]'

log_header "$STACK_NAME cleanup complete"
