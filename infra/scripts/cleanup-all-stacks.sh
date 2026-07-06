#!/usr/bin/env bash
# Cleanup every CertRa-* stack in reverse-dependency order.
#
# Order matters: app-level stacks first (Maintenance, Migrations,
# Workers, App) so they release their ENIs + listener attachments
# from NetworkStack's ALB before NetworkStack is itself torn down.
# Foundation stacks (Network, Data, Dns, Secrets, Observability,
# Temporal) come next. IdentityStack last because the cfn-exec
# boundary it owns has to stay attached until the other stacks are
# fully gone — otherwise other-stack delete operations would lose
# the IAM scoping that the boundary provides.
#
# Each per-stack script confirms the env name interactively. To
# avoid 11 prompts, this wrapper passes `CLEANUP_AUTO_CONFIRM=1`
# which makes the per-stack confirm a no-op — BUT it still requires
# one master confirmation here. If you want to be prompted per
# stack, run each script individually instead.
#
# Usage: ENV=staging ./infra/scripts/cleanup-all-stacks.sh

set -euo pipefail

ENV="${ENV:-staging}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"

log_header "MASTER CLEANUP: cert-ra-${ENV}"
echo
echo "About to destroy EVERY CertRa-* stack for env '${ENV}'."
echo "This will:"
echo "  - Drain + delete every ECS service"
echo "  - Empty + delete S3 buckets"
echo "  - Delete RDS instance + master secret"
echo "  - Schedule ACM PCAs for 7-day deletion (\$400/mo each stops)"
echo "  - Schedule all CMKs for 7-day deletion"
echo "  - Force-delete every CFN stack"
echo
read -r -p "Type 'NUKE ${ENV}' to confirm: " confirm
[[ "$confirm" == "NUKE ${ENV}" ]] || { echo "Aborted."; exit 1; }

export CLEANUP_AUTO_CONFIRM=1

# Reverse-dependency order. App-level first so ENIs / listeners
# release. Identity last because cfn-exec-boundary is attached to
# the bootstrap role and CFN delete elsewhere relies on it.
for script in \
    cleanup-maintenance-stack.sh \
    cleanup-migrations-stack.sh \
    cleanup-workers-stack.sh \
    cleanup-app-stack.sh \
    cleanup-temporal-stack.sh \
    cleanup-observability-stack.sh \
    cleanup-secrets-stack.sh \
    cleanup-dns-stack.sh \
    cleanup-data-stack.sh \
    cleanup-network-stack.sh \
    cleanup-identity-stack.sh; do
    log_header "Running $script"
    ENV="$ENV" CLEANUP_AUTO_CONFIRM=1 bash "$SCRIPT_DIR/$script" || {
        echo "$script failed; continuing with the next stack."
    }
done

log_header "MASTER CLEANUP COMPLETE"
echo
echo "Some resources are in 7-day pending deletion (CMKs, ACM PCAs)."
echo "They'll fully disappear after the schedule completes."
echo "Cloudflare NS delegation for the env domain still points at the"
echo "now-deleted Route53 zone; update it after the next initial-setup.sh."
