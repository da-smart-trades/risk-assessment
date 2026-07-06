#!/usr/bin/env bash
# Tear down CertRa-IdentityStack-${ENV} and all retained env-scoped
# resources: ECR repo (cert-ra-${ENV}), 3 GHA OIDC roles
# (gha-cert-ra-{build,sign,deploy}-${ENV}), cosign-pubkey SSM param
# (/cert-ra/${ENV}/signing/cosign-pubkey), cfn-exec-boundary managed
# policy (cert-ra-cfn-exec-boundary-${ENV}, must be detached from the
# bootstrap cfn-exec-role first), and the two CMKs
# (alias/cert-ra-signing-${ENV}, alias/cert-ra-ecr-${ENV}).
#
# IMPORTANT: the GitHub OIDC provider is account-level and SHARED
# across envs. We never delete it here — deleting it would break the
# OTHER env's GHA roles. Delete it manually with the AWS console only
# when both env IdentityStacks are gone.
#
# Usage: ENV=staging ./infra/scripts/cleanup-identity-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-IdentityStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

ECR_REPO="cert-ra-${ENV}"
COSIGN_PARAM="/cert-ra/${ENV}/signing/cosign-pubkey"
BOUNDARY_NAME="cert-ra-cfn-exec-boundary-${ENV}"

# 1. ECR repo with --force to delete tagged images too.
log_step "Deleting ECR repository $ECR_REPO"
aws ecr delete-repository \
    --repository-name "$ECR_REPO" --force 2>&1 | grep -v '^$' || true

# 2. The 3 GHA OIDC IAM roles. Detach all policies + inline first.
for ROLE in "gha-cert-ra-build-${ENV}" "gha-cert-ra-sign-${ENV}" "gha-cert-ra-deploy-${ENV}"; do
    log_step "Deleting IAM role $ROLE"
    for ARN in $(aws iam list-attached-role-policies \
        --role-name "$ROLE" --query 'AttachedPolicies[].PolicyArn' \
        --output text 2>/dev/null); do
        aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$ARN"
    done
    for NAME in $(aws iam list-role-policies \
        --role-name "$ROLE" --query 'PolicyNames' --output text 2>/dev/null); do
        aws iam delete-role-policy --role-name "$ROLE" --policy-name "$NAME"
    done
    aws iam delete-role --role-name "$ROLE" 2>&1 | grep -v '^$' || true
done

# 3. Cosign pubkey SSM parameter.
log_step "Deleting SSM parameter $COSIGN_PARAM"
aws ssm delete-parameter \
    --name "$COSIGN_PARAM" 2>&1 | grep -v '^$' || true

# 4. cfn-exec-boundary managed policy. First detach from cfn-exec-role.
log_step "Detaching + deleting $BOUNDARY_NAME"
BOUNDARY_ARN="arn:aws:iam::${ACCOUNT_ID}:policy/${BOUNDARY_NAME}"

# Detach from any roles that have it as a boundary.
for ROLE in $(aws iam list-entities-for-policy \
    --policy-arn "$BOUNDARY_ARN" \
    --query 'PolicyRoles[].RoleName' --output text 2>/dev/null); do
    echo "  removing boundary from $ROLE"
    aws iam delete-role-permissions-boundary --role-name "$ROLE" 2>/dev/null \
        || aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$BOUNDARY_ARN" 2>/dev/null \
        || true
done

# Delete non-default versions.
for VID in $(aws iam list-policy-versions \
    --policy-arn "$BOUNDARY_ARN" \
    --query 'Versions[?!IsDefaultVersion].VersionId' --output text 2>/dev/null); do
    aws iam delete-policy-version --policy-arn "$BOUNDARY_ARN" --version-id "$VID"
done

aws iam delete-policy --policy-arn "$BOUNDARY_ARN" 2>&1 \
    | grep -v '^$' || true

# 5. Force-delete the CFN stack (abandons the 2 CMKs under RETAIN).
force_delete_cfn_stack "$STACK_NAME"

# 6. KMS CMKs (env-suffixed aliases).
cleanup_kms_cmk_by_alias "cert-ra-ecr-${ENV}"
cleanup_kms_cmk_by_alias "cert-ra-signing-${ENV}"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "ECR repo" aws ecr describe-repositories --repository-names "$ECR_REPO"
for ROLE in "gha-cert-ra-build-${ENV}" "gha-cert-ra-sign-${ENV}" "gha-cert-ra-deploy-${ENV}"; do
    verify_resource_gone "IAM role $ROLE" aws iam get-role --role-name "$ROLE"
done
verify_resource_gone "Boundary policy" aws iam get-policy --policy-arn "$BOUNDARY_ARN"
echo "  GitHub OIDC provider: SHARED — not deleted. Remove manually if both envs are gone."

log_header "$STACK_NAME cleanup complete"
