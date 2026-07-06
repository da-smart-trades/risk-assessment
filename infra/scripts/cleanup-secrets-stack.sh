#!/usr/bin/env bash
# Tear down CertRa-SecretsStack-${ENV}.
#
# Provisions 12 SeededSecrets (7 app: OAuth, RPC, session, email,
# Sentry, Anthropic, The Graph + 5 Temporal mTLS shells) and one
# CMK (alias/cert-ra-secrets). SeededSecrets have RemovalPolicy.RETAIN;
# they survive stack delete and need force-delete-without-recovery
# (default 7-30 day recovery window).
#
# Usage: ENV=staging ./infra/scripts/cleanup-secrets-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-SecretsStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. Force-delete every secret under /cert-ra/${ENV}/. This is
#    immediate (no recovery window), so the next initial-setup.sh
#    won't trip on `ResourceExistsException`.
log_step "Force-deleting all /cert-ra/${ENV}/* secrets"
for SECRET in $(aws secretsmanager list-secrets \
    --query "SecretList[?starts_with(Name, '/cert-ra/${ENV}/')].ARN" \
    --output text 2>/dev/null); do
    NAME=$(echo "$SECRET" | sed 's|.*:secret:||; s|-[A-Za-z0-9]*$||')
    aws secretsmanager delete-secret \
        --secret-id "$SECRET" \
        --force-delete-without-recovery >/dev/null \
        && echo "  $NAME: deleted" \
        || echo "  $NAME: error"
done

# 2. Force-delete the CFN stack (abandons the secrets CMK).
force_delete_cfn_stack "$STACK_NAME"

# 3. CMK.
cleanup_kms_cmk_by_alias "cert-ra-secrets-${ENV}"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "Secrets under /cert-ra/${ENV}/" aws secretsmanager list-secrets --query "SecretList[?starts_with(Name, '/cert-ra/${ENV}/')]"

log_header "$STACK_NAME cleanup complete"
