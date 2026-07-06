#!/usr/bin/env bash
# Diagnose why invited users are NOT prompted to set a password.
#
# The set-a-password activation card (invitation/accept) renders only when
# ALL of these hold (see _invitation_accept.py get_invitation_page):
#   - invitation.kind = 'first_time_activation'
#   - invitation.user_id IS NOT NULL  (pre-provisioned User row)
#   - that user's activated_at IS NULL AND hashed_password IS NULL
#   - neither invitation.force_provider NOR team.enforced_provider is set
#     (either one redirects straight into OIDC, skipping the password step)
#
# This query lists every non-accepted, non-revoked, unexpired invitation
# and reports which gate it trips, so we can see what the affected users
# have in common.
#
# Usage:
#   ENV=prod    ./diagnose-invitation-activation.sh
#   ENV=staging ./diagnose-invitation-activation.sh
#
# Requires an active AWS session for cert-ra-${ENV}-upgrader (the
# permission set with ecs:ExecuteCommand on the maint cluster).

set -euo pipefail

ENV="${ENV:-prod}"
AWS_REGION="${AWS_REGION:-us-east-2}"
PROFILE="${AWS_PROFILE:-cert-ra-${ENV}-upgrader}"

read -r -d '' SQL <<'EOF' || true
SELECT ti.email,
       t.name                          AS team_name,
       ti.kind,
       (ti.user_id IS NOT NULL)        AS has_user_id,
       ti.force_provider,
       t.enforced_provider,
       u.activated_at,
       (u.hashed_password IS NOT NULL) AS has_password,
       CASE
         WHEN ti.kind IS DISTINCT FROM 'first_time_activation'
              THEN 'kind not first_time_activation (legacy/null) -> no password prompt'
         WHEN ti.user_id IS NULL
              THEN 'no pre-provisioned user_id -> no password prompt'
         WHEN COALESCE(ti.force_provider, t.enforced_provider) IS NOT NULL
              THEN 'forced OIDC provider -> redirected to SSO, password skipped'
         WHEN u.activated_at IS NOT NULL
              THEN 'user already activated -> password step is over'
         WHEN u.hashed_password IS NOT NULL
              THEN 'user already has a password -> password step is over'
         ELSE 'OK: should show the set-password card'
       END                             AS diagnosis
FROM team_invitation ti
JOIN team t          ON t.id = ti.team_id
LEFT JOIN user_account u ON u.id = ti.user_id
WHERE ti.is_accepted = false
  AND ti.revoked_at IS NULL
  AND (ti.expires_at IS NULL OR ti.expires_at > now())
ORDER BY diagnosis, ti.created_at DESC;
EOF

echo ">>> [${ENV}] resolving maint service..." >&2
MAINT_SERVICE=$(aws cloudformation describe-stacks \
    --stack-name "CertRa-MaintenanceStack-${ENV}" \
    --region "$AWS_REGION" \
    --profile "$PROFILE" \
    --query "Stacks[0].Outputs[?OutputKey=='ServiceName'].OutputValue" \
    --output text)
if [[ -z "$MAINT_SERVICE" || "$MAINT_SERVICE" == "None" ]]; then
    echo "FATAL: no ServiceName output on CertRa-MaintenanceStack-${ENV}" >&2
    exit 1
fi

echo ">>> [${ENV}] finding running maint task in ${MAINT_SERVICE}..." >&2
MAINT_TASK=$(aws ecs list-tasks \
    --cluster "cert-ra-maint-${ENV}" \
    --service-name "$MAINT_SERVICE" \
    --region "$AWS_REGION" \
    --profile "$PROFILE" \
    --query 'taskArns[0]' --output text)
if [[ -z "$MAINT_TASK" || "$MAINT_TASK" == "None" ]]; then
    echo "FATAL: no running task in cert-ra-maint-${ENV}/${MAINT_SERVICE}" >&2
    exit 1
fi
echo ">>> task: ${MAINT_TASK}" >&2

# Base64-encode the SQL so nested-quote escaping through aws-cli + SSM
# agent + `sh -c` stays sane. The maint image has GNU coreutils so
# `base64 -d` is available.
SQL_B64=$(printf '%s' "$SQL" | base64 -w0)

echo ">>> executing psql via ECS Exec (output below)..." >&2
echo >&2

aws ecs execute-command \
    --cluster "cert-ra-maint-${ENV}" \
    --task "$MAINT_TASK" \
    --container Maint \
    --interactive \
    --region "$AWS_REGION" \
    --profile "$PROFILE" \
    --command "sh -c 'echo $SQL_B64 | base64 -d | PGPASSWORD=\$DATABASE_PASSWORD psql -h \$DATABASE_HOST -p \$DATABASE_PORT -U \$DATABASE_USER -d cert_ra -X -q'"
