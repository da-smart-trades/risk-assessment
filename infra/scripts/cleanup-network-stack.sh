#!/usr/bin/env bash
# Tear down CertRa-NetworkStack-${ENV}.
#
# NetworkStack provisions: VPC + subnets + IGW + NATs + 7 SGs +
# interface/gateway VPC endpoints + public ALB with deletion
# protection. Most of these delete cleanly via FORCE_DELETE_STACK,
# but the ALB's deletion protection has to come off first, and
# Lambda-owned ENIs (from BeforeAllowTraffic/AfterAllowTraffic
# hooks) can linger ~30 min after AppStack tears down.
#
# Run cleanup-app-stack.sh BEFORE this script — the ALB has
# listeners that AppStack owns, and dependent ENIs from app + maint
# tasks have to clear before the SGs can delete.
#
# Usage: ENV=staging ./infra/scripts/cleanup-network-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-NetworkStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. ALB deletion protection. PublicAlb construct sets this; CFN
#    can't delete the ALB while it's on.
log_step "Disabling ALB deletion protection"
ALB_ARN=$(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?LoadBalancerName=='cert-ra-alb'].LoadBalancerArn | [0]" \
    --output text 2>/dev/null || echo "None")
if [[ "$ALB_ARN" != "None" && -n "$ALB_ARN" ]]; then
    aws elbv2 modify-load-balancer-attributes \
        --load-balancer-arn "$ALB_ARN" \
        --attributes Key=deletion_protection.enabled,Value=false >/dev/null
    echo "  cert-ra-alb deletion protection: OFF"
fi

# 2. CFN force-delete. CFN handles the bulk of NetworkStack cleanup;
#    VPC delete will fail loudly if dependent ENIs still exist.
force_delete_cfn_stack "$STACK_NAME"

# 3. Hunt for stranded ENIs that may still reference cert-ra SGs.
log_step "Checking for stranded ENIs on cert-ra security groups"
for SG_NAME in cert-ra-alb-sg cert-ra-app-sg cert-ra-worker-sg \
    cert-ra-temporal-fe-sg cert-ra-maint-sg cert-ra-migrate-sg cert-ra-rds-sg; do
    SG_ID=$(aws ec2 describe-security-groups \
        --filters Name=group-name,Values="$SG_NAME" \
        --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
    if [[ "$SG_ID" == "None" || -z "$SG_ID" ]]; then continue; fi

    for ENI in $(aws ec2 describe-network-interfaces \
        --filters Name=group-id,Values="$SG_ID" \
        --query 'NetworkInterfaces[].NetworkInterfaceId' \
        --output text 2>/dev/null); do
        echo "  Deleting stranded ENI $ENI"
        aws ec2 delete-network-interface \
            --network-interface-id "$ENI" 2>&1 | grep -v '^$' || true
    done

    # Try deleting the SG itself (will fail if other resources reference it).
    aws ec2 delete-security-group --group-id "$SG_ID" 2>&1 \
        | grep -v '^$' || true
done

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ALB cert-ra-alb" aws elbv2 describe-load-balancers --names cert-ra-alb
verify_resource_gone "VPC cert-ra-*" aws ec2 describe-vpcs --filters Name=tag:Name,Values="cert-ra-${ENV}-vpc"

log_header "$STACK_NAME cleanup complete"
echo
echo "Note: If VPC delete failed due to stranded ENIs, wait 30 min and"
echo "re-run this script. Lambda-owned ENIs auto-release on a slow cycle."
