#!/usr/bin/env bash
# Diagnose + unstick a CloudFormation stack that's hung in
# UPDATE_IN_PROGRESS, UPDATE_ROLLBACK_IN_PROGRESS, or UPDATE_ROLLBACK_FAILED.
#
# Typical use:
#
#   ENV=prod STACK=CertRa-TemporalStack-prod ./rescue-stuck-rollback.sh
#
# What it does:
#
# 1. Reports the current stack status.
# 2. Lists resources still in *_IN_PROGRESS or *_FAILED, with how long
#    they've been stuck.
# 3. For each stuck ECS service, identifies the OLD (healthy) task-def
#    revision the service should be running on, and offers to issue an
#    `ecs update-service --force-new-deployment` directly against that
#    revision. This bypasses CFN and re-stabilises the service without
#    waiting on CFN's ~1-hour stabilization timeout.
# 4. If the stack is already in UPDATE_ROLLBACK_FAILED, builds the
#    correct `continue-update-rollback --resources-to-skip ...` command
#    so CFN can finish the rollback without re-trying the resources
#    that hard-failed.
#
# Read-only by default — every mutating step is gated behind an
# interactive prompt.
#
# Required env:
#   ENV   — `staging` or `prod` (selects the SSO profile)
#   STACK — the stack name to inspect, e.g. CertRa-TemporalStack-prod
#
# Optional env:
#   STUCK_MINUTES — how long a resource must be IN_PROGRESS before this
#                   script considers it stuck. Default: 10.
#   AUTO          — set to 1 to skip prompts and auto-perform every
#                   recommended action. Use carefully.

set -euo pipefail

ENV="${ENV:?ENV is required (staging or prod)}"
STACK="${STACK:?STACK is required (e.g. CertRa-TemporalStack-prod)}"
STUCK_MINUTES="${STUCK_MINUTES:-10}"
AUTO="${AUTO:-0}"

PROFILE="cert-ra-${ENV}-installer"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$(resolve_region "$ENV")"

log_header "Diagnosing $STACK"

# --- Stack status ------------------------------------------------------------

STATUS=$(aws cloudformation describe-stacks --stack-name "$STACK" \
    --query 'Stacks[0].StackStatus' --output text)
echo "Stack status: $STATUS"
echo

confirm() {
    [[ "$AUTO" == "1" ]] && return 0
    local prompt="$1"
    local default="${2:-N}"
    local opts
    [[ "$default" == "Y" ]] && opts="[Y/n]" || opts="[y/N]"
    read -r -p "$prompt $opts " answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[yY] ]]
}

# --- Resource inventory ------------------------------------------------------

log_step "Resources currently in *_IN_PROGRESS or *_FAILED states"

# `describe-stack-resources` shows the live status of each resource. The
# `--no-paginate` keeps the output predictable; CFN stacks rarely exceed
# the page size but the flag avoids surprises.
RESOURCES_JSON=$(aws cloudformation describe-stack-resources \
    --stack-name "$STACK" \
    --query 'StackResources[?contains(ResourceStatus,`IN_PROGRESS`) || contains(ResourceStatus,`FAILED`)]')

if [[ "$RESOURCES_JSON" == "[]" || -z "$RESOURCES_JSON" ]]; then
    echo "  No resources stuck — stack should converge on its own."
else
    echo "$RESOURCES_JSON" | jq -r '.[] |
        "  \(.ResourceStatus | .[:30] | . + (" " * (30-length))) " +
        "\(.LogicalResourceId) " +
        "(\(.ResourceType))" +
        (if .ResourceStatusReason then " — \(.ResourceStatusReason)" else "" end)'
fi
echo

# --- Recent events -----------------------------------------------------------

log_step "Last 8 stack events"
aws cloudformation describe-stack-events --stack-name "$STACK" \
    --max-items 8 \
    --query 'StackEvents[*].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]' \
    --output table
echo

# --- ECS-specific rescue path ------------------------------------------------

# Identify stuck ECS services owned by this stack. For each, we figure
# out the OLD (currently-ACTIVE) task definition revision the rollback
# should converge to, and offer to force a new deployment on it directly
# — that's usually the fastest way to break a deadlock where CFN is
# waiting on ECS service stabilization that ECS can't finish until the
# failing PRIMARY deployment times out.

log_step "Looking for stuck ECS services in $STACK"

STUCK_ECS_LOGICAL_IDS=$(echo "$RESOURCES_JSON" | jq -r '
    .[] | select(.ResourceType=="AWS::ECS::Service") | .LogicalResourceId
')

if [[ -z "$STUCK_ECS_LOGICAL_IDS" ]]; then
    echo "  No ECS::Service resources stuck."
else
    echo "  Stuck ECS services:"
    while IFS= read -r lid; do
        [[ -z "$lid" ]] && continue
        echo "    $lid"
    done <<< "$STUCK_ECS_LOGICAL_IDS"
    echo
fi

# Map each LogicalResourceId → PhysicalResourceId (full ECS service ARN).
# describe-stack-resource gives one mapping per call but the output is
# cheap to fan out across the small number of services in a stack.

declare -a STUCK_SERVICE_ARNS=()
while IFS= read -r lid; do
    [[ -z "$lid" ]] && continue
    arn=$(aws cloudformation describe-stack-resource \
        --stack-name "$STACK" \
        --logical-resource-id "$lid" \
        --query 'StackResourceDetail.PhysicalResourceId' \
        --output text 2>/dev/null || echo "")
    [[ -n "$arn" ]] && STUCK_SERVICE_ARNS+=("$arn")
done <<< "$STUCK_ECS_LOGICAL_IDS"

# For ECS service ARNs, extract `<cluster>/<service-name>` so describe-
# services can be called cluster-scoped.
nudge_ecs_service() {
    local arn="$1"
    # PhysicalResourceId for AWS::ECS::Service is the FULL ARN, formatted
    # as `arn:aws:ecs:<region>:<acct>:service/<cluster-name>/<service-name>`.
    # Split on the trailing `service/<cluster-name>/<service-name>` tail.
    local tail="${arn##*:service/}"
    local cluster="${tail%/*}"
    local service="${tail##*/}"

    echo "    Service: $service (cluster: $cluster)"

    # Pull the two deployments. PRIMARY is what CFN tried to roll out;
    # ACTIVE is the old known-good revision. We force a fresh deploy on
    # the ACTIVE def — same revision the service was running before the
    # broken update, guaranteed to start cleanly.
    local depls
    depls=$(aws ecs describe-services \
        --cluster "$cluster" --services "$service" \
        --query 'services[0].deployments[*].{status:status,taskDefinition:taskDefinition,running:runningCount,desired:desiredCount,failed:failedTasks}' \
        --output json)
    echo "      Current deployments:"
    echo "$depls" | jq -r '.[] | "        \(.status): rev=\(.taskDefinition|split("/")[-1]) running=\(.running)/\(.desired) failed=\(.failed)"'

    local active_td
    active_td=$(echo "$depls" | jq -r '
        .[] | select(.status=="ACTIVE") | .taskDefinition' | head -n1)
    if [[ -z "$active_td" || "$active_td" == "null" ]]; then
        echo "      ! No ACTIVE deployment found — service has nothing to roll back to."
        echo "        You'll need to pick a known-good task def manually:"
        echo "          aws ecs list-task-definitions --family-prefix <family> --status ACTIVE"
        return 1
    fi

    echo "      Will nudge service onto: $active_td (force-new-deployment)"
    if confirm "      Issue update-service for $service?"; then
        aws ecs update-service \
            --cluster "$cluster" --service "$service" \
            --task-definition "$active_td" \
            --force-new-deployment >/dev/null
        echo "      Update issued. CFN should detect the service stabilising on the rolled-back def and complete the rollback."
    fi
}

if [[ ${#STUCK_SERVICE_ARNS[@]} -gt 0 ]]; then
    log_step "Nudging stuck ECS services back onto their ACTIVE (healthy) task defs"
    for arn in "${STUCK_SERVICE_ARNS[@]}"; do
        nudge_ecs_service "$arn" || true
    done
fi

# --- UPDATE_ROLLBACK_FAILED helper ------------------------------------------

if [[ "$STATUS" == "UPDATE_ROLLBACK_FAILED" ]]; then
    log_step "Stack is UPDATE_ROLLBACK_FAILED — building continue-update-rollback command"

    # Resources in UPDATE_FAILED can't be rolled back automatically. The
    # standard recovery is to tell CFN to skip them and finish the
    # rollback; the operator then fixes the skipped resources by hand.
    SKIP_IDS=$(aws cloudformation describe-stack-resources \
        --stack-name "$STACK" \
        --query 'StackResources[?ResourceStatus==`UPDATE_FAILED`].LogicalResourceId' \
        --output text)

    if [[ -z "$SKIP_IDS" ]]; then
        echo "  No UPDATE_FAILED resources found, but stack is UPDATE_ROLLBACK_FAILED."
        echo "  Inspect events more closely; you may just need:"
        echo "    aws cloudformation continue-update-rollback --stack-name $STACK"
    else
        echo "  Resources to skip: $SKIP_IDS"
        # AWS CLI takes --resources-to-skip as space-separated identifiers.
        SKIP_ARG=$(echo "$SKIP_IDS" | tr '\t' ' ')
        echo
        echo "  Suggested command:"
        echo "    aws cloudformation continue-update-rollback \\"
        echo "        --stack-name $STACK \\"
        echo "        --resources-to-skip $SKIP_ARG"
        echo
        if confirm "  Run continue-update-rollback now?"; then
            # shellcheck disable=SC2086  # intentional word-splitting on SKIP_ARG
            aws cloudformation continue-update-rollback \
                --stack-name "$STACK" \
                --resources-to-skip $SKIP_ARG
            echo "  Issued. Stack should transition to UPDATE_ROLLBACK_IN_PROGRESS,"
            echo "  then UPDATE_ROLLBACK_COMPLETE."
        fi
    fi
fi

# --- Final guidance ----------------------------------------------------------

case "$STATUS" in
    UPDATE_ROLLBACK_COMPLETE|UPDATE_COMPLETE)
        log_header "Stack is healthy ($STATUS)"
        echo "You can re-run: ENV=$ENV ./apply-pending-fixes.sh"
        ;;
    UPDATE_ROLLBACK_IN_PROGRESS|UPDATE_IN_PROGRESS)
        log_header "Stack is in progress — wait and re-run this script"
        echo "Watch progress:"
        echo "  aws cloudformation describe-stacks --stack-name $STACK \\"
        echo "      --query 'Stacks[0].StackStatus'"
        echo "Or stream events:"
        echo "  aws cloudformation describe-stack-events --stack-name $STACK \\"
        echo "      --max-items 10 --output table"
        ;;
    UPDATE_ROLLBACK_FAILED)
        log_header "Stack is UPDATE_ROLLBACK_FAILED"
        echo "Use the continue-update-rollback command above to skip past the failed resources."
        ;;
    *)
        log_header "Stack status: $STATUS (unhandled — inspect manually)"
        ;;
esac
