#!/usr/bin/env bash
# One-off operator script: apply the recent infra fixes (commits
# 234a8b1, 816c808, 811c2a8) to an environment without re-running
# initial-setup.sh end-to-end.
#
# Touched stacks (and why this script is needed):
#   - CertRa-DataStack-${ENV}        — S3 CMK now delegates to IAM via the
#                                       S3 service (kms:ViaService), so the
#                                       app's task role can call KMS when
#                                       PutObject/GetObject pass through it.
#                                       Without this, SSE-KMS uploads fail
#                                       with KMS.AccessDenied even though
#                                       the task role's identity policy
#                                       allows kms:Encrypt+Decrypt+GenerateDataKey.
#   - CertRa-SecretsStack-${ENV}     — new /cert-ra/${ENV}/temporal/mtls/app shell
#   - CertRa-TemporalStack-${ENV}    — frontend_endpoint:7233 + new app cert
#                                       config; entrypoint shim now writes
#                                       cert/key/chain to /tmp/temporal-tls
#                                       (the non-root temporal user can't
#                                       mkdir in /run); cert renewal Lambda
#                                       fired inline to populate the new
#                                       shell (InitialCertIssuance no-ops
#                                       on Update)
#   - CertRa-MaintenanceStack-${ENV} — A4 deny-list now covers app mTLS
#   - CertRa-AppStack-${ENV}         — CSRF allowlist, Secure cookies, S3
#                                       storage env + IAM grants, app mTLS
#                                       triplet injection
#   - CertRa-WorkersStack-${ENV}     — CERT_RA_TEMPORAL_ALERTS_ENABLED moved
#                                       to alerts worker; :7233 endpoint
#
# Requires the CertRaInstaller permission set — the foundation stacks
# above are outside CertRaUpgrader's IAM scope, so upgrade.sh can't be
# used for the full sequence.
#
# Usage:
#   ENV=staging ./apply-pending-fixes.sh
#   ENV=prod    ./apply-pending-fixes.sh
#
# Options:
#   SKIP_APP_REDEPLOY=1   — skip the AppStack+WorkersStack deploy
#                           (use if you'd rather run upgrade.sh next
#                           against a fresh image SHA)

set -euo pipefail

ENV="${ENV:-staging}"
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

log_header "Applying pending infra fixes for cert-ra-${ENV}"

require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

ACCOUNT_ID=$(aws --profile "$PROFILE" sts get-caller-identity \
    --query Account --output text)
echo
echo "About to deploy DataStack, SecretsStack, TemporalStack,"
echo "MaintenanceStack, AppStack, WorkersStack to:"
echo "    Account:        $ACCOUNT_ID"
echo "    Region:         $REGION"
echo "    Environment:    $ENV"
echo "    Permission set: $EXPECTED_PERMISSION_SET"
echo
read -r -p "Continue? [y/N] " confirm
[[ "$confirm" == "y" || "$confirm" == "Y" ]] || { echo "Aborted."; exit 1; }

cd "$INFRA_DIR"
uv sync --frozen

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"
export CDK_ENV="$ENV"
# mTLS is already enforced in steady state; don't toggle it off-then-on
# the way initial-setup.sh does on a clean install.
export CDK_TEMPORAL_MTLS_ENFORCE=true

# Step 0: DataStack — the only resource that actually changes here is
# the S3 CMK's key policy (adds a DelegateToIamViaService statement so
# in-account principals can call kms:GenerateDataKey when the call is
# routed through S3). Quick in-place update; no S3 / RDS resource
# replacement. Must precede AppStack so the new app task tries SSE-KMS
# uploads against an already-updated key policy.
log_step "Deploying DataStack (S3 CMK delegate-to-IAM policy)"
cdk_run deploy "CertRa-DataStack-${ENV}" --require-approval=broadening

# Step 1: SecretsStack — create the /cert-ra/${ENV}/temporal/mtls/app
# shell. Must precede TemporalStack so its InitialCertIssuance Custom
# Resource can resolve the secret ARN at synth/deploy.
log_step "Deploying SecretsStack (creates app mTLS shell)"
cdk_run deploy "CertRa-SecretsStack-${ENV}" --require-approval=broadening

# Step 2: TemporalStack — picks up the new service in InitialCertIssuance
# + CertRenewal config and updates the frontend_endpoint CFN output to
# include :7233.
log_step "Deploying TemporalStack (frontend_endpoint:7233 + app cert config)"
cdk_run deploy "CertRa-TemporalStack-${ENV}" --require-approval=broadening

# Step 3: Fire the cert renewal Lambda once so the new app cert is
# actually issued. InitialCertIssuance deliberately no-ops on Update
# (to avoid racing with the renewal path on existing certs); the
# renewal handler treats any empty / unparseable secret as
# `days_remaining=None` and reissues it.
log_step "Triggering cert renewal Lambda to issue the new app cert"

RENEWAL_FN=$(aws cloudformation describe-stack-resources \
    --stack-name "CertRa-TemporalStack-${ENV}" \
    --query "StackResources[?contains(LogicalResourceId,'CertRenewalHandler') \
        && ResourceType=='AWS::Lambda::Function'].PhysicalResourceId | [0]" \
    --output text)
if [[ -z "$RENEWAL_FN" || "$RENEWAL_FN" == "None" ]]; then
    echo "FATAL: could not find CertRenewalHandler in TemporalStack-${ENV}" >&2
    exit 1
fi

# The EventBridge rule's Input already carries the right services list
# (including the new "app" entry, since we just redeployed). Pulling
# it from the rule means we don't have to reconstruct the payload by
# hand and it stays in lock-step with the deployed stack.
RULE_NAME=$(aws cloudformation describe-stack-resources \
    --stack-name "CertRa-TemporalStack-${ENV}" \
    --query "StackResources[?contains(LogicalResourceId,'DailySchedule') \
        && ResourceType=='AWS::Events::Rule'].PhysicalResourceId | [0]" \
    --output text)
if [[ -z "$RULE_NAME" || "$RULE_NAME" == "None" ]]; then
    echo "FATAL: could not find CertRenewal DailySchedule rule" >&2
    exit 1
fi
PAYLOAD=$(aws events list-targets-by-rule --rule "$RULE_NAME" \
    --query 'Targets[0].Input' --output text)

# Sanity-check that the redeployed rule actually mentions the new
# service — otherwise the invocation would no-op and we'd silently
# leave the shell empty.
if ! echo "$PAYLOAD" | jq -e '.Services[] | select(.Name=="app")' >/dev/null; then
    echo "FATAL: rule payload does not include the 'app' service —" >&2
    echo "       TemporalStack-${ENV} probably did not pick up the new commit." >&2
    exit 1
fi

INVOKE_OUT=$(mktemp)
trap 'rm -f "$INVOKE_OUT"' EXIT
aws lambda invoke \
    --function-name "$RENEWAL_FN" \
    --payload "$PAYLOAD" \
    --cli-binary-format raw-in-base64-out \
    "$INVOKE_OUT" >/dev/null

echo "Renewal handler returned:"
jq < "$INVOKE_OUT"

# Success criterion: the `app` secret has a parseable cert. The handler
# returns it in either `renewed` (we just minted it) or `skipped` (a
# prior invocation minted it and it's not near expiry — happens
# naturally when this script is re-run after a partial deploy). Either
# way the secret is populated and AppStack can mount it.
if ! jq -e '(.renewed + .skipped) | index("app") != null' < "$INVOKE_OUT" >/dev/null; then
    echo "FATAL: renewal handler did NOT report 'app' in either renewed or skipped." >&2
    echo "Inspect the Lambda logs:" >&2
    echo "    aws logs tail /aws/lambda/${RENEWAL_FN} --since 5m --follow" >&2
    exit 1
fi

# Belt-and-suspenders: confirm the secret actually has a `cert` field
# with PEM content (catches the case where the handler skipped because
# of an exception swallow we didn't catch).
APP_SECRET_NAME="/cert-ra/${ENV}/temporal/mtls/app"
if ! aws secretsmanager get-secret-value \
        --secret-id "$APP_SECRET_NAME" \
        --query 'SecretString' --output text 2>/dev/null \
        | jq -e '.cert | startswith("-----BEGIN CERTIFICATE-----")' >/dev/null; then
    echo "FATAL: ${APP_SECRET_NAME} does not contain a valid PEM cert." >&2
    echo "The Lambda likely failed silently or hasn't issued the cert yet." >&2
    echo "Inspect:" >&2
    echo "    aws logs tail /aws/lambda/${RENEWAL_FN} --since 30m --format short" >&2
    exit 1
fi
echo "App cert present in ${APP_SECRET_NAME}."

# Step 4: MaintenanceStack — A4 deny-list now covers app mTLS shell.
# Also picks up the new frontend_endpoint:7233 value via the
# TEMPORAL_ADDRESS env var on the maint task definition.
log_step "Deploying MaintenanceStack (deny-list + :7233)"
cdk_run deploy "CertRa-MaintenanceStack-${ENV}" --require-approval=broadening

# Step 5: AppStack + WorkersStack — config-only changes (no new image
# tag here; the existing image is reused with the new task-def env).
if [[ "${SKIP_APP_REDEPLOY:-0}" == "1" ]]; then
    log_step "SKIPPING AppStack + WorkersStack (SKIP_APP_REDEPLOY=1)"
    echo "Run upgrade.sh with a new IMAGE_SHA to roll the app + workers."
else
    log_step "Deploying AppStack + WorkersStack (new task-def revisions, no image bump)"
    cdk_run deploy \
        "CertRa-AppStack-${ENV}" \
        "CertRa-WorkersStack-${ENV}" \
        --require-approval=broadening

    # AppStack uses CodeDeploy (deployment_controller=CODE_DEPLOY) — CDK
    # only registers the new task-definition revision. We must fire a
    # CodeDeploy deployment to actually shift traffic to the new revision.
    log_step "Triggering CodeDeploy blue/green for AppStack"
    APP_TD=$(stack_output "CertRa-AppStack-${ENV}" "AppTaskDefinitionArn")
    CD_APP=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployApplicationName")
    CD_DG=$(stack_output "CertRa-AppStack-${ENV}" "CodeDeployDeploymentGroupName")
    APPSPEC=$(jq -n --arg td "$APP_TD" '{
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
    if ! aws deploy wait deployment-successful --deployment-id "$DEPLOYMENT_ID"; then
        echo "Blue/green deploy failed — inspect:" >&2
        echo "    aws deploy get-deployment --deployment-id $DEPLOYMENT_ID" >&2
        exit 1
    fi
fi

# Step 6: smoke test the public hostname (Route53 alias → ALB → ECS).
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
echo "App: https://${DOMAIN}"
