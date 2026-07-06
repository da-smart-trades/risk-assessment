#!/usr/bin/env bash
# Initial AWS deployment for cert-ra. Idempotent — safe to re-run if a
# step fails.
#
# Prerequisites:
#   - AWS SSO profile `cert-ra-${ENV}-installer` configured.
#   - `uv`, `bunx`/`cdk`, `jq`, `python3`, `node`/`npm` installed locally.
#   - `lending-markets-rating/` (vendored in-tree). This script will
#     `npm install` its Node dependencies before the deploy starts.
#
# Usage:
#   ENV=staging ./initial-setup.sh
#   ENV=prod    ./initial-setup.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
EXPECTED_PERMISSION_SET="CertRaInstaller"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"

# Per-env CDK output directory so concurrent staging + prod runs don't
# collide on cdk.out (CDK refuses with "Other CLIs are reading from
# cdk.out" if two synths share an output dir).
CDK_OUT_DIR="cdk.out.${ENV}"

# Wrapper around `bunx cdk` that always passes --output. Use this
# instead of `bunx cdk` directly for any synth/diff/deploy/bootstrap
# call so concurrent runs stay isolated.
cdk_run() {
    bunx cdk --output "$CDK_OUT_DIR" "$@"
}

source "$SCRIPT_DIR/_common.sh"

# Region comes from env_config.region (_config.py) — the same source of
# truth the CDK app deploys to — unless AWS_REGION explicitly overrides
# it. Resolved here (not at the top) because resolve_region lives in
# _common.sh, which is sourced just above.
REGION="$(resolve_region "$ENV")"
# Public domain (env_config.domain) — used to look up the DnsStack hosted
# zone so its NS records can be printed during the deploy (see Step 4).
DOMAIN="$(resolve_domain "$ENV")"

log_header "Initial setup for cert-ra-${ENV}"

# Step 1: ensure SSO session is valid and matches the Installer
# permission set. Wrong role = hard-fail before any infra deploy.
require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

# Step 2: operator confirmation. Show the account/region/permission-set
# so the operator can sanity-check before triggering any deploys.
ACCOUNT_ID=$(aws --profile "$PROFILE" sts get-caller-identity \
    --query Account --output text)
echo
echo "About to bootstrap and deploy ALL foundation + app stacks into:"
echo "    Account:        $ACCOUNT_ID"
echo "    Region:         $REGION"
echo "    Environment:    $ENV"
echo "    Permission set: $EXPECTED_PERMISSION_SET"
echo
read -r -p "Continue? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

cd "$INFRA_DIR"
uv sync --frozen

# lending-markets-rating is a vendored TypeScript/Node CLI the
# Dockerfile COPYs into the cert-ra runtime image. Its node_modules
# need to be present on disk at Docker-build time; otherwise the
# image lands without them and the worker invocations fail at
# runtime. Install before any `cdk deploy` that would trigger a
# Docker asset build.
REPO_ROOT="$(dirname "$INFRA_DIR")"
if [[ -d "$REPO_ROOT/lending-markets-rating" ]]; then
    log_step "Installing lending-markets-rating Node dependencies"
    # flock serialises concurrent staging + prod runs so they don't
    # race on the shared node_modules / package-lock files. The lock
    # lives under /tmp so it's auto-cleaned on reboot but shared
    # between processes.
    (
        flock -x 9
        cd "$REPO_ROOT/lending-markets-rating" && npm install
    ) 9>/tmp/cert-ra-lending-markets-npm.lock
fi

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export CDK_ENV="$ENV"

# Step 2.5: ensure the account-level GitHub OIDC provider exists.
# AWS allows ONE provider per (account, URL). With per-env IdentityStacks
# both stacks would race to create it; we create it once out of band so
# both stacks just import-by-ARN. Idempotent — re-runs check first.
log_step "Ensuring account-level GitHub OIDC provider"
OIDC_PROVIDER_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
if ! aws iam get-open-id-connect-provider \
    --open-id-connect-provider-arn "$OIDC_PROVIDER_ARN" >/dev/null 2>&1; then
    aws iam create-open-id-connect-provider \
        --url "https://token.actions.githubusercontent.com" \
        --client-id-list "sts.amazonaws.com" \
        --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" \
        >/dev/null
    echo "  created OIDC provider $OIDC_PROVIDER_ARN"
else
    echo "  OIDC provider $OIDC_PROVIDER_ARN already exists; reusing."
fi

# Step 3.0: vanilla CDK bootstrap. Resolves a chicken-and-egg between
# step 3a and 3b — step 3a's `cdk deploy CertRa-IdentityStack` needs
# the CDKToolkit stack to already exist, but step 3b's bootstrap
# wants to attach the M1 cfn-exec boundary policy which only exists
# after 3a runs. The intermediate vanilla bootstrap stands up
# CDKToolkit without the boundary so 3a can proceed; 3b then
# re-bootstraps (idempotent) to attach the boundary on top.
log_step "Initial CDK bootstrap (no boundary yet)"
cdk_run bootstrap "aws://${ACCOUNT_ID}/${REGION}"

# Step 3a: deploy IdentityStack so the cfn-exec boundary policy
# exists. The boundary is itself a managed IAM policy; the next
# bootstrap step (3b) needs it to exist before it can attach it to
# the cfn-exec-role.
log_step "Pre-deploying IdentityStack for cfn-exec boundary"
cdk_run deploy "CertRa-IdentityStack-${ENV}" --require-approval=broadening

# Step 3b: re-bootstrap with the per-env custom permissions boundary (M1).
# Idempotent — picks up boundary changes and attaches the policy to
# the cfn-exec role's permissions boundary.
log_step "Re-bootstrapping CDK with custom-permissions-boundary"
cdk_run bootstrap "aws://${ACCOUNT_ID}/${REGION}" \
    --custom-permissions-boundary "cert-ra-cfn-exec-boundary-${ENV}"

# Step 4: foundation stacks, deployed in dependency order. IdentityStack
# was already deployed in 3a. TemporalStack is deployed twice: first
# with mTLS enforcement disabled (so workers can bootstrap), then
# re-deployed in step 6.5 with mTLS enforcement on. See § Temporal
# mTLS (M5) "Backward compat during rollout" in the design spec.
export CDK_TEMPORAL_MTLS_ENFORCE=false
for stack in \
    "CertRa-NetworkStack-${ENV}" \
    "CertRa-DataStack-${ENV}" \
    "CertRa-DnsStack-${ENV}" \
    "CertRa-SecretsStack-${ENV}" \
    "CertRa-ObservabilityStack-${ENV}" \
    "CertRa-TemporalStack-${ENV}"; do
    log_step "Deploying $stack"
    if [[ "$stack" == "CertRa-DnsStack-${ENV}" ]]; then
        # DnsStack's ACM cert is DNS-validated against the Route53 zone
        # this stack creates, but the zone's NS must be delegated at
        # Cloudflare before validation (and thus this deploy) can finish.
        # The zone is created in the first ~minute; the cert then blocks
        # on validation. Background a poller that prints the NS as soon as
        # the zone exists so the operator can set up delegation while the
        # deploy waits. cdk stays in the foreground (normal progress +
        # approval handling). The poller exits on its own after printing;
        # kill it as a safety net once the deploy returns.
        await_and_print_zone_ns "$DOMAIN" &
        ns_poll_pid=$!
        cdk_run deploy "$stack" --require-approval=broadening
        kill "$ns_poll_pid" 2>/dev/null || true
        wait "$ns_poll_pid" 2>/dev/null || true
    elif [[ "$stack" == "CertRa-TemporalStack-${ENV}" ]]; then
        # The Temporal server services can't start until the RDS schema
        # exists, but the schema-bootstrap task (created by THIS stack) must
        # be run out-of-band via `aws ecs run-task`. Background it: it waits
        # for the cluster + task def to appear, runs the one-off bootstrap,
        # and the services then stabilise so this deploy can complete. cdk
        # stays in the foreground. Without this the deploy hangs (services
        # never reach steady state).
        run_temporal_schema_bootstrap "$ENV" &
        schema_pid=$!
        cdk_run deploy "$stack" --require-approval=broadening
        wait "$schema_pid" 2>/dev/null || true
    else
        cdk_run deploy "$stack" --require-approval=broadening
    fi
done

# Step 5: seed secrets (interactive). The operator pastes real values
# for the OAuth/RPC/Sentry/session/Resend secrets. mTLS secrets are
# auto-populated by InitialCertIssuance during TemporalStack's deploy
# and so are skipped here.
log_step "Seeding secrets — paste values when prompted"
# Run via `uv run` so the script gets the infra venv (which provides
# boto3) — bare `python3` is the system interpreter and has no boto3.
# cwd is $INFRA_DIR (cd'd above), so uv resolves this project.
uv run python "$SCRIPT_DIR/seed-secrets.py" --env "$ENV"

# Step 6: app stacks. MigrationsStack first so the migration task def
# exists; AppStack registers its initial task def (CodeDeploy controller
# so no rollout until upgrade.sh triggers one); MaintenanceStack starts
# its long-lived task. WorkersStack is deliberately deferred until
# AFTER step 6.6 (default namespace creation) — without the namespace,
# the worker tasks crash on their first DescribeNamespace call and the
# WorkersStack create silently rolls back to ROLLBACK_COMPLETE.
#
# CDK_APP_BOOTSTRAP=1 is set ONLY for the AppStack iteration below —
# it makes AppStack register the ECS service with desired_count=0 so
# CFN doesn't wait for tasks to stabilise on a not-yet-pushed image.
# Without it, the first AppStack create would time out and roll back
# because the `cert-ra-${env}:latest` tag doesn't exist in ECR yet.
# upgrade.sh's first run pins a real image SHA and scales desired
# back up to DEFAULT_DESIRED_COUNT.
for stack in \
    "CertRa-MigrationsStack-${ENV}" \
    "CertRa-AppStack-${ENV}" \
    "CertRa-MaintenanceStack-${ENV}"; do
    log_step "Deploying $stack"
    if [[ "$stack" == "CertRa-AppStack-${ENV}" ]]; then
        CDK_APP_BOOTSTRAP=1 cdk_run deploy "$stack" --require-approval=broadening
    else
        cdk_run deploy "$stack" --require-approval=broadening
    fi
done

# Step 6.5 (M5): re-deploy TemporalStack with mTLS enforcement on now
# that all services have their per-service certs from SecretsStack
# (InitialCertIssuance populated them during the first TemporalStack
# deploy in step 4).
log_step "Re-deploying TemporalStack with mTLS enforcement enabled"
export CDK_TEMPORAL_MTLS_ENFORCE=true
cdk_run deploy "CertRa-TemporalStack-${ENV}" --require-approval=broadening

# Step 6.6: create the Temporal `default` namespace via the maint
# container. `temporalio/server:1.27.4` (the non-auto-setup image we
# use) does NOT auto-create namespaces; without this step the first
# worker tasks crash-loop on NamespaceNotFound errors. The maint
# container's `temporal` CLI wrapper handles mTLS automatically, so
# this just needs the env's mTLS-on Temporal cluster (which step 6.5
# just stood up). Idempotent — re-runs do nothing if the namespace
# already exists.
create_temporal_default_namespace

# Step 6.7: WorkersStack — now safe to deploy because (a) the
# namespace exists, so worker startup succeeds, and (b) the Temporal
# frontend enforces mTLS, so workers use their per-service certs from
# SecretsStack to authenticate.
log_step "Deploying CertRa-WorkersStack-${ENV}"
cdk_run deploy "CertRa-WorkersStack-${ENV}" --require-approval=broadening

# Step 7: run initial DB migration. The migration task is one-off; it
# runs `certora-risk-api database upgrade` (alembic upgrade head) and
# exits 0 on success.
log_step "Running initial app DB migration"
run_migration_task

# Step 7.5: seed the manual-metrics tables from the canonical payloads
# packaged inside the image. Each console entry point connects to RDS using
# the migrate task's credentials and seeds its scoped subset.
#
#   - governance: seeded ONCE. The seeder is guarded — it no-ops if any
#     GOVERNANCE row already exists, so operator UI edits survive a re-run
#     of initial-setup.sh. upgrade.sh also invokes it (guarded, no --force),
#     so an environment that was never seeded gets backfilled on upgrade;
#     existing rows are left untouched.
#   - tokens: seeded ONCE, same guard as governance — TOKEN_RISK is
#     operator-owned, so a re-run / upgrade never clobbers UI edits.
#   - protocol: replace-from-JSON per protocol; idempotent, also re-run on
#     every upgrade by upgrade.sh.
log_step "Seeding manual-metrics tables from packaged payloads"
run_seed_script "governance" certora-risk-seed-governance
run_seed_script "tokens" certora-risk-seed-tokens
run_seed_script "protocol" certora-risk-seed-metrics

# Step 8: post-deploy smoke test. The ALB hostname is in the
# NetworkStack outputs; Route53 resolves the env domain to it via the
# alias A-record that AppStack PR 3 added.
log_step "Smoke test"
ALB_DNS=$(stack_output "CertRa-NetworkStack-${ENV}" "AlbDnsName")
if curl --fail --silent --show-error "https://${ALB_DNS}/landing/" >/dev/null; then
    echo "OK"
else
    echo "Smoke test failed — check ECS service events:"
    echo "  aws ecs describe-services --cluster cert-ra-app-${ENV}-cluster \\"
    echo "      --services cert-ra-app-${ENV}"
    exit 1
fi

# Step 9: end-to-end verification. Catches the silent-failure mode where
# a stack ends in ROLLBACK_COMPLETE (terminal state — `cdk deploy`
# returns 0 anyway because it's a valid CFN outcome) or an ECS service
# stabilises but doesn't reach desired count. Both happened in prod
# during the initial cert-ra rollout and went unnoticed for months
# because no step actively inspected stack/service health after each
# deploy. verify-deploy.sh is the standalone check; running it here
# turns initial-setup into a self-validating end-to-end exercise.
log_step "Verifying every stack + service ended in a healthy state"
if ! "$SCRIPT_DIR/verify-deploy.sh"; then
    echo "Verification failed — see output above." >&2
    echo "Initial setup is INCOMPLETE; do not consider this env shipped." >&2
    exit 1
fi

DOMAIN=$(stack_output "CertRa-DnsStack-${ENV}" "DomainName")
log_header "Initial setup complete"
echo "ALB:    https://${ALB_DNS}"
echo "Domain: https://${DOMAIN} (resolves once Cloudflare NS delegation"
echo "        for ${DOMAIN} points at the Route53 NameServers output"
echo "        from CertRa-DnsStack-${ENV})"
