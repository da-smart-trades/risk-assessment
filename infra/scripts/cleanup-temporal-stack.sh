#!/usr/bin/env bash
# Tear down CertRa-TemporalStack-${ENV}.
#
# Heavy lift. TemporalStack provisions:
# - Two ACM PCAs (root + subordinate, $400/mo each — important to delete!)
# - 5 SeededSecret mTLS shells (lived in SecretsStack; not our concern)
# - ECS cluster + 4 Fargate services (Frontend / History / Matching /
#   Internal-Worker) + custom Docker image
# - Internal NLB with deletion_protection
# - SchemaBootstrap one-off task def
# - InitialCertIssuance + CertRenewal + RootCaDisable Lambdas (with VPC ENIs)
#
# ACM PCA notes:
# - Subordinate must be DISABLED first, then 7-day pending deletion
# - Root must be DISABLED (RootCaDisable already does this on deploy)
#   then deleted, also with 7-day pending window
# - Both incur PCA-hours billing until permanently deleted
#
# Usage: ENV=staging ./infra/scripts/cleanup-temporal-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-TemporalStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. Disable the internal NLB's deletion protection.
log_step "Disabling internal NLB deletion protection"
for NLB_ARN in $(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?starts_with(LoadBalancerName, 'cert-ra-temporal') && Scheme=='internal'].LoadBalancerArn" \
    --output text 2>/dev/null); do
    aws elbv2 modify-load-balancer-attributes \
        --load-balancer-arn "$NLB_ARN" \
        --attributes Key=deletion_protection.enabled,Value=false >/dev/null
    echo "  $(basename "$NLB_ARN"): protection OFF"
done

# 2. Drain + delete the 4 ECS services so the ENIs they hold release.
log_step "Draining ECS Fargate services"
CLUSTER_NAME="cert-ra-temporal-${ENV}"
for SVC in $(aws ecs list-services --cluster "$CLUSTER_NAME" \
    --query 'serviceArns[]' --output text 2>/dev/null); do
    echo "  scaling down + deleting $SVC"
    aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SVC" \
        --desired-count 0 >/dev/null 2>&1 || true
    aws ecs delete-service --cluster "$CLUSTER_NAME" --service "$SVC" \
        --force >/dev/null 2>&1 || true
done

# 3. Force-delete the CFN stack. Lambdas + cluster + NLB go with it.
force_delete_cfn_stack "$STACK_NAME"

# 4. ACM PCAs. Subordinate first (depends on root for signing), then root.
log_step "Disabling + scheduling deletion of ACM PCAs"
for CA_TYPE in SUBORDINATE ROOT; do
    for CA_ARN in $(aws acm-pca list-certificate-authorities \
        --query "CertificateAuthorities[?Type=='${CA_TYPE}' && Status!='DELETED'].Arn" \
        --output text 2>/dev/null); do
        echo "  PCA ($CA_TYPE): $CA_ARN"
        aws acm-pca update-certificate-authority \
            --certificate-authority-arn "$CA_ARN" \
            --status DISABLED 2>/dev/null || true
        aws acm-pca delete-certificate-authority \
            --certificate-authority-arn "$CA_ARN" \
            --permanent-deletion-time-in-days 7 2>&1 | grep -v '^$' || true
    done
done

# 5. Stranded log groups owned by Temporal services.
for LG in /ecs/cert-ra-temporal-frontend /ecs/cert-ra-temporal-history \
    /ecs/cert-ra-temporal-matching /ecs/cert-ra-temporal-internal-worker \
    /ecs/cert-ra-temporal-schema-bootstrap; do
    cleanup_log_group "$LG"
done

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECS cluster" aws ecs describe-clusters --clusters "$CLUSTER_NAME" --query 'clusters[?status==`ACTIVE`]'
verify_resource_gone "ACM PCAs" aws acm-pca list-certificate-authorities --query "CertificateAuthorities[?Status!='DELETED']"

log_header "$STACK_NAME cleanup complete"
echo
echo "ACM PCAs are scheduled for permanent deletion in 7 days. Billing"
echo "($400/mo each) STOPS once the schedule begins."
