#!/usr/bin/env bash
# Resume apply-pending-fixes.sh from step 4 (MaintenanceStack), after
# the renewal Lambda has already populated /cert-ra/${ENV}/temporal/mtls/app.
#
# Use this when the previous run bailed at the renewal step but the
# `app` cert is actually present — for example, when an earlier
# invocation issued the cert and a follow-up invocation correctly
# skipped it because it isn't near expiry.
#
# Steps it runs:
#   4. CertRa-MaintenanceStack-${ENV}           (deny-list + :7233)
#   5a. CertRa-AppStack-${ENV} + WorkersStack    (config-only redeploy)
#   5b. CodeDeploy blue/green for AppStack       (waits for traffic shift)
#   6.  curl https://${DOMAIN}/landing/ smoke test
#
# Requires the CertRaInstaller permission set (same as the parent script).
#
# Usage:
#   ENV=prod    ./finish-pending-fixes.sh
#   ENV=staging ./finish-pending-fixes.sh
#
# Options:
#   SKIP_CERT_CHECK=1   Skip the upfront `app` cert verification.
#                       Use only if openssl isn't available locally
#                       (we trust the renewal Lambda's output instead).

set -euo pipefail

ENV="${ENV:-prod}"
PROFILE="cert-ra-${ENV}-installer"
EXPECTED_PERMISSION_SET="CertRaInstaller"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
CDK_OUT_DIR="cdk.out.${ENV}"

cdk_run() {
    bunx cdk --output "$CDK_OUT_DIR" "$@"
}

source "$SCRIPT_DIR/_common.sh"

REGION="$(resolve_region "$ENV")"

log_header "Finishing pending infra fixes for cert-ra-${ENV}"

require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export CDK_ENV="$ENV"
export CDK_TEMPORAL_MTLS_ENFORCE=true

cd "$INFRA_DIR"
uv sync --frozen

# --- Pre-flight: confirm the app mTLS secret has a real cert -----------------
# If this fails the rest of the deploy is pointless — AppStack tasks
# would mount empty content and crash on Temporal connect. The check is
# defensive belt-and-suspenders against the script being run by mistake.

if [[ "${SKIP_CERT_CHECK:-0}" != "1" ]]; then
    log_step "Verifying /cert-ra/${ENV}/temporal/mtls/app has a real cert"
    APP_SECRET_NAME="/cert-ra/${ENV}/temporal/mtls/app"
    APP_CERT_PEM=$(aws secretsmanager get-secret-value \
        --secret-id "$APP_SECRET_NAME" \
        --query 'SecretString' --output text 2>/dev/null \
        | jq -r '.cert // empty')
    if [[ -z "$APP_CERT_PEM" ]] \
       || ! echo "$APP_CERT_PEM" | grep -q "BEGIN CERTIFICATE"; then
        echo "FATAL: ${APP_SECRET_NAME} does not contain a PEM-encoded cert." >&2
        echo "       Run apply-pending-fixes.sh through step 3 first, or" >&2
        echo "       invoke the CertRenewal Lambda manually." >&2
        exit 1
    fi
    # If openssl is available, print subject + dates so the operator
    # sees what's about to be mounted into the app container.
    if command -v openssl >/dev/null 2>&1; then
        echo "$APP_CERT_PEM" | openssl x509 -noout -subject -issuer -dates \
            | sed 's/^/    /'
    else
        echo "    (openssl not installed; trusting Secrets Manager content)"
    fi
fi

# --- Step 4: MaintenanceStack ------------------------------------------------
# Updates the maint task role's A4 deny-list to cover the new
# `/cert-ra/${env}/temporal/mtls/app*` shell (renames the statement Sid
# from DenyReadWorkerMtlsSecrets to DenyReadPeerMtlsSecrets) and picks
# up the new frontend_endpoint:7233 in the task env. Single-task
# Fargate service rolling deploy; usually stabilises in ~2 min.

log_step "Deploying CertRa-MaintenanceStack-${ENV}"
cdk_run deploy "CertRa-MaintenanceStack-${ENV}" --require-approval=broadening

# --- Step 5a: AppStack + WorkersStack ----------------------------------------
# Registers new task-definition revisions with the new env (CSRF
# allowlist, secure cookies, S3 storage, app mTLS triplet) and IAM
# grants (S3 bucket + S3 CMK). AppStack uses the CodeDeploy controller
# so this only REGISTERS the revision — CodeDeploy traffic shift comes
# next. WorkersStack uses rolling + circuit breaker.

log_step "Deploying CertRa-AppStack-${ENV} + CertRa-WorkersStack-${ENV}"
cdk_run deploy \
    "CertRa-AppStack-${ENV}" \
    "CertRa-WorkersStack-${ENV}" \
    --require-approval=broadening

# --- Step 5b: CodeDeploy blue/green for AppStack ----------------------------
# Build an AppSpec pointing at the new task def revision and create a
# deployment. Staging uses linear 10%/min (~10 min); prod uses canary
# 10%/5min + bake (~15-20 min). The BeforeAllowTraffic Lambda probes
# /landing/ on the test listener before any production traffic shifts.

log_step "Triggering CodeDeploy blue/green for AppStack"
APP_TASK_DEF_ARN=$(stack_output "CertRa-AppStack-${ENV}" "AppTaskDefinitionArn")
CD_APP=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployApplicationName")
CD_DG=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployDeploymentGroupName")

APPSPEC=$(jq -n --arg td "$APP_TASK_DEF_ARN" '{
    version: "0.0",
    Resources: [{
        TargetService: {
            Type: "AWS::ECS::Service",
            Properties: {
                TaskDefinition: $td,
                LoadBalancerInfo: { ContainerName: "App", ContainerPort: 8000 }
            }
        }
    }]
}')
DEPLOYMENT_ID=$(aws deploy create-deployment \
    --application-name "$CD_APP" \
    --deployment-group-name "$CD_DG" \
    --revision "revisionType=AppSpecContent,appSpecContent={content='${APPSPEC}'}" \
    --query 'deploymentId' --output text)
echo "CodeDeploy deployment: $DEPLOYMENT_ID"
echo "(staging = linear 10%/min ~10 min; prod = canary 10% + bake ~15-20 min)"

if ! aws deploy wait deployment-successful --deployment-id "$DEPLOYMENT_ID"; then
    echo "Blue/green deploy failed or rolled back — inspect:" >&2
    echo "    aws deploy get-deployment --deployment-id $DEPLOYMENT_ID" >&2
    exit 1
fi

# --- Step 6: smoke test ------------------------------------------------------
# Production URL → Route53 alias → ALB → ECS. /landing/ confirms the
# app container is up and the ALB target is healthy.

log_step "Smoke test"
DOMAIN=$(stack_output "CertRa-DnsStack-${ENV}" "DomainName")
if curl --fail --silent --show-error "https://${DOMAIN}/landing/" >/dev/null; then
    echo "OK"
else
    echo "Smoke test failed — check ECS service events:" >&2
    echo "    aws ecs describe-services --cluster cert-ra-app-${ENV}-cluster \\" >&2
    echo "        --services cert-ra-app-${ENV}" >&2
    exit 1
fi

log_header "Done"
echo "App:      https://${DOMAIN}"
echo
echo "Things to verify manually:"
echo "  - Workers are connected to Temporal via mTLS (check worker logs"
echo "    for 'temporal: starting' without TLS errors):"
echo "      aws logs tail /ecs/cert-ra-worker-metrics-${ENV} --since 5m"
echo "      aws logs tail /ecs/cert-ra-worker-alerts-${ENV} --since 5m"
echo "  - Avatar upload writes to S3 (log in, change profile pic, confirm"
echo "    object lands in cert-ra-assets-${ENV})."
echo "  - Login flow works end-to-end (CSRF allowlist + Secure cookies)."
