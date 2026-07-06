#!/usr/bin/env bash
# Shared helpers for cleanup-*-stack.sh scripts.
#
# Source this file (after `_common.sh`) at the top of each per-stack
# cleanup script. Provides:
#
#   confirm_cleanup_intent <STACK_NAME>    — SSO check + operator must
#                                            type ENV literally
#   force_delete_cfn_stack <STACK_NAME>    — FORCE_DELETE_STACK + wait
#   cleanup_kms_cmk_by_alias <alias-name>  — strip MFA deny, drop alias,
#                                            schedule key deletion (7d)
#   cleanup_log_group <name>               — delete one CW log group
#   verify_resource_gone <label> <test-cmd>— print CLEAN / STILL PRESENT

# Caller is responsible for `set -euo pipefail` and for setting:
#   ENV, PROFILE, REGION, ACCOUNT_ID

confirm_cleanup_intent() {
    local stack_name="$1"
    require_sso_session "$PROFILE" "CertRaInstaller"

    ACCOUNT_ID=$(aws --profile "$PROFILE" sts get-caller-identity \
        --query Account --output text)
    echo
    echo "About to PERMANENTLY DELETE all resources owned by:"
    echo "    Account:     $ACCOUNT_ID"
    echo "    Region:      $REGION"
    echo "    Environment: $ENV"
    echo "    Stack:       $stack_name"
    echo

    # Master cleanup script sets CLEANUP_AUTO_CONFIRM=1 to skip the
    # per-stack prompts (it has its own master confirmation upstream).
    if [[ "${CLEANUP_AUTO_CONFIRM:-0}" != "1" ]]; then
        read -r -p "Type the env name '${ENV}' to confirm: " confirm
        [[ "$confirm" == "$ENV" ]] || { echo "Aborted."; exit 1; }
    else
        echo "CLEANUP_AUTO_CONFIRM=1; skipping per-stack confirm."
    fi

    export AWS_PROFILE="$PROFILE"
    export AWS_REGION="$REGION"
}

force_delete_cfn_stack() {
    local stack_name="$1"
    log_step "Force-deleting CloudFormation stack $stack_name"

    local status
    status=$(aws cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_EXISTS")

    if [[ "$status" == "NOT_EXISTS" ]]; then
        echo "  $stack_name does not exist; skipping."
        return
    fi

    aws cloudformation delete-stack \
        --stack-name "$stack_name" \
        --deletion-mode FORCE_DELETE_STACK
    aws cloudformation wait stack-delete-complete \
        --stack-name "$stack_name" 2>/dev/null \
        || echo "  wait failed; stack may already be gone."
}

# Strip the MFA-gated `DenyKeyDeletionWithoutMfa` from the key policy
# (using the AccountRootPolicyUpdate Sid's unconditional PutKeyPolicy
# grant), drop the alias, then schedule the key for 7-day deletion.
cleanup_kms_cmk_by_alias() {
    local alias_name="$1"  # e.g. "cert-ra-rds"
    log_step "Cleaning up KMS alias/${alias_name} + underlying key"

    local key_id
    key_id=$(aws kms describe-key \
        --key-id "alias/${alias_name}" \
        --query 'KeyMetadata.KeyId' --output text 2>/dev/null || echo "")

    if [[ -z "$key_id" ]]; then
        echo "  alias/${alias_name} does not exist; skipping."
        return
    fi

    local policy_file="/tmp/${alias_name//\//-}-policy.json"

    aws kms get-key-policy \
        --key-id "$key_id" --policy-name default \
        --query 'Policy' --output text > "$policy_file"

    python3 - <<EOF
import json
p = json.load(open("$policy_file"))
p["Statement"] = [s for s in p["Statement"]
                  if s.get("Sid") != "DenyKeyDeletionWithoutMfa"]
p["Statement"].append({
    "Sid": "TempUnblockDelete",
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::${ACCOUNT_ID}:root"},
    "Action": "kms:*",
    "Resource": "*",
})
json.dump(p, open("$policy_file", "w"))
EOF

    aws kms put-key-policy \
        --key-id "$key_id" --policy-name default \
        --policy "file://${policy_file}" >/dev/null

    aws kms delete-alias --alias-name "alias/${alias_name}" 2>&1 \
        | grep -v '^$' || true

    aws kms schedule-key-deletion \
        --key-id "$key_id" \
        --pending-window-in-days 7 \
        --query 'DeletionDate' --output text 2>&1 \
        | grep -v '^$' || true

    rm -f "$policy_file"
}

cleanup_log_group() {
    local name="$1"
    aws logs delete-log-group --log-group-name "$name" 2>/dev/null \
        && echo "  log group $name: deleted" \
        || true
}

# verify_resource_gone <label> <command...>
# Runs the command; if its output is empty (or matches "does not exist"),
# prints "  $label: CLEAN" else "  $label: STILL PRESENT".
verify_resource_gone() {
    local label="$1"
    shift
    local output
    output=$("$@" 2>&1 || true)
    if [[ -z "$output" ]] || [[ "$output" == *"does not exist"* ]] \
        || [[ "$output" == *"NoSuch"* ]] || [[ "$output" == "None" ]]; then
        echo "  $label: CLEAN"
    else
        echo "  $label: STILL PRESENT: $output"
    fi
}
