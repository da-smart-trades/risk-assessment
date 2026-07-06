#!/usr/bin/env bash
# Verify a cert-ra environment is fully healthy.
#
# Checks:
#   1. Every CFN stack ends in a healthy terminal state
#      (CREATE_COMPLETE or UPDATE_COMPLETE — NOT ROLLBACK_COMPLETE
#      or *_ROLLBACK_*).
#   2. Every ECS service has runningCount == desiredCount.
#   3. The public /landing/ endpoint returns 200.
#
# Exits non-zero on any failure with a one-line summary per problem.
#
# Why this exists: prior to having this check, initial-setup.sh judged
# success by `cdk deploy` exit code, which is 0 for both CREATE_COMPLETE
# and ROLLBACK_COMPLETE. Two stacks (Workers, Maintenance) sat in
# ROLLBACK_COMPLETE undetected for months because nothing actively
# inspected the cluster state after a deploy.
#
# Usage:
#   ENV=prod    ./verify-deploy.sh
#   ENV=staging ./verify-deploy.sh
#
# Designed to be safe to run anytime — pure read-only AWS API calls.

set -uo pipefail
# NOTE: not `set -e` — we want to collect *all* failures, not fail at
# the first. Errors are accumulated and reported at the end.

ENV="${ENV:-staging}"
PROFILE="${AWS_PROFILE:-cert-ra-${ENV}-installer}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$(resolve_region "$ENV")"

# Healthy terminal states. UPDATE_COMPLETE_CLEANUP_IN_PROGRESS is a
# transient final-success state where CFN is dropping replaced resources
# in the background — treat as healthy.
HEALTHY_STATES=(
    CREATE_COMPLETE
    UPDATE_COMPLETE
    UPDATE_COMPLETE_CLEANUP_IN_PROGRESS
)

# Stacks in the cert-ra topology. Order doesn't matter for the verify
# pass; this mirrors the foundation→app deploy order in initial-setup.sh
# only for readability.
STACKS=(
    CertRa-IdentityStack
    CertRa-NetworkStack
    CertRa-DataStack
    CertRa-DnsStack
    CertRa-SecretsStack
    CertRa-ObservabilityStack
    CertRa-TemporalStack
    CertRa-MigrationsStack
    CertRa-AppStack
    CertRa-WorkersStack
    CertRa-MaintenanceStack
)

# ECS clusters that should host running services. Each cluster's services
# are discovered via list-services so the verify pass doesn't have to
# hard-code service names that may shift between releases.
CLUSTERS=(
    "cert-ra-app-${ENV}-cluster"
    "cert-ra-workers-${ENV}"
    "cert-ra-maint-${ENV}"
    "cert-ra-temporal-${ENV}"
    "cert-ra-migrations-${ENV}"
)

# `failures` accumulates one short message per problem; final summary
# walks it and exits 1 if non-empty.
declare -a failures=()

log_header "Verifying cert-ra-${ENV}"
echo "Account: $(aws sts get-caller-identity --query Account --output text)"
echo "Region:  ${AWS_REGION}"
echo

# --- Stack states -----------------------------------------------------------

log_step "Checking stack states"

for short in "${STACKS[@]}"; do
    stack="${short}-${ENV}"
    status=$(aws cloudformation describe-stacks --stack-name "$stack" \
        --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "MISSING")

    if [[ "$status" == "MISSING" ]]; then
        printf "  %-40s %s\n" "$stack" "✗ MISSING"
        failures+=("$stack does not exist")
        continue
    fi

    healthy=0
    for ok in "${HEALTHY_STATES[@]}"; do
        [[ "$status" == "$ok" ]] && { healthy=1; break; }
    done

    if (( healthy )); then
        printf "  %-40s %s\n" "$stack" "✓ $status"
    else
        printf "  %-40s %s\n" "$stack" "✗ $status"
        failures+=("$stack is in state $status (expected one of: ${HEALTHY_STATES[*]})")
    fi
done

# --- ECS service health -----------------------------------------------------

log_step "Checking ECS service health (running == desired)"

# Migrations cluster is special: it hosts a task definition family but
# no services (operators trigger one-off tasks via `aws ecs run-task`).
# We skip it during service checks but still want to confirm the cluster
# exists.

for cluster in "${CLUSTERS[@]}"; do
    cluster_info=$(aws ecs describe-clusters --clusters "$cluster" \
        --query 'clusters[0].{name:clusterName,status:status}' \
        --output text 2>/dev/null)
    if [[ -z "$cluster_info" || "$cluster_info" == *"None"* ]]; then
        printf "  %-50s %s\n" "$cluster" "✗ CLUSTER MISSING"
        failures+=("ECS cluster $cluster does not exist")
        continue
    fi

    # `list-services` returns nothing for a service-less cluster (migrations).
    svc_arns=$(aws ecs list-services --cluster "$cluster" \
        --query 'serviceArns[]' --output text 2>/dev/null)
    if [[ -z "$svc_arns" ]]; then
        printf "  %-50s %s\n" "$cluster" "(no services — expected for migrations cluster)"
        continue
    fi

    # describe-services takes up to 10 services per call; we have at most
    # 4 per cluster so a single call is fine.
    # shellcheck disable=SC2086 — intentional word-splitting on svc_arns
    svc_state=$(aws ecs describe-services --cluster "$cluster" \
        --services $svc_arns \
        --query 'services[*].{name:serviceName,running:runningCount,desired:desiredCount,rolloutState:deployments[0].rolloutState,failed:deployments[0].failedTasks}' \
        --output json)

    # Iterate per-service with jq so each service surfaces as its own line.
    while IFS=$'\t' read -r name running desired rollout_state failed; do
        [[ -z "$name" ]] && continue
        # CodeDeploy-controlled services (AppStack's litestar service)
        # have `rolloutState=null` because the controller is CodeDeploy,
        # not ECS. Treat null as healthy if running==desired. jq's @tsv
        # renders JSON null as an empty field, so accept "" alongside
        # the literal "None"/"null" strings.
        if [[ "$running" == "$desired" ]] \
           && { [[ "$rollout_state" == "COMPLETED" ]] || [[ "$rollout_state" == "None" ]] || [[ "$rollout_state" == "null" ]] || [[ -z "$rollout_state" ]]; } \
           && { [[ "$failed" == "0" ]] || [[ "$failed" == "None" ]] || [[ "$failed" == "null" ]] || [[ -z "$failed" ]]; }; then
            printf "  %-50s ✓ %s/%s (rollout=%s)\n" "$name" "$running" "$desired" "$rollout_state"
        else
            printf "  %-50s ✗ running=%s desired=%s rollout=%s failed=%s\n" \
                "$name" "$running" "$desired" "$rollout_state" "$failed"
            failures+=("$cluster/$name unhealthy: running=$running/$desired rollout=$rollout_state failed=$failed")
        fi
    done < <(echo "$svc_state" | jq -r '.[] | [.name, .running, .desired, .rolloutState, .failed] | @tsv')
done

# --- /landing/ probe --------------------------------------------------------

log_step "Checking /landing/ endpoint"

domain=$(aws cloudformation describe-stacks \
    --stack-name "CertRa-DnsStack-${ENV}" \
    --query "Stacks[0].Outputs[?OutputKey=='DomainName'].OutputValue" \
    --output text 2>/dev/null)

if [[ -z "$domain" || "$domain" == "None" ]]; then
    printf "  %-50s %s\n" "<no domain>" "✗ Could not read DnsStack domain"
    failures+=("Could not read DomainName output from CertRa-DnsStack-${ENV}")
else
    if curl --fail --silent --show-error --max-time 10 "https://${domain}/landing/" >/dev/null; then
        printf "  %-50s %s\n" "https://${domain}/landing/" "✓ 200 OK"
    else
        printf "  %-50s %s\n" "https://${domain}/landing/" "✗ Did not return 2xx"
        failures+=("https://${domain}/landing/ did not return 2xx")
    fi
fi

# --- Final summary ----------------------------------------------------------

echo
if (( ${#failures[@]} == 0 )); then
    log_header "All checks passed for cert-ra-${ENV}"
    exit 0
fi

log_header "FAILED — ${#failures[@]} problem(s) detected"
for f in "${failures[@]}"; do
    echo "  • $f"
done
echo
echo "Recovery hints:"
echo "  - ROLLBACK_COMPLETE  → stack must be deleted before it can be recreated."
echo "                          \`aws cloudformation delete-stack --stack-name <name>\`"
echo "  - *_ROLLBACK_IN_PROGRESS → wait for it to settle, then re-run this script."
echo "  - Unhealthy ECS service → inspect logs:"
echo "      aws logs tail /ecs/<service-name> --since 30m --format short"
echo "  - /landing/ not 200    → check ALB target health + the app log group:"
echo "      aws logs tail /ecs/cert-ra-app-${ENV} --since 30m --format short"
exit 1
