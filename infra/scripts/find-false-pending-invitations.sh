#!/usr/bin/env bash
# Find team_invitation rows the UI calls "Pending" where the user is
# in fact already a member of the team. These come from two paths
# that don't flip is_accepted=true:
#   1. OIDC sign-in's best-effort claim silently fails (token lost
#      from session, email mismatch, force_provider mismatch, etc.)
#      — see _oidc.py:480-512.
#   2. Admin add_member bypass — creates TeamMember directly without
#      touching any pending invitation for that (team, email).
#
# Usage:
#   ENV=prod    ./find-false-pending-invitations.sh
#   ENV=staging ./find-false-pending-invitations.sh
#
# Requires an active AWS session for cert-ra-${ENV}-upgrader (the
# permission set with ecs:ExecuteCommand on the maint cluster).
# Redirect stdout to capture results:
#   ENV=prod ./find-false-pending-invitations.sh > out

set -euo pipefail

ENV="${ENV:-prod}"
AWS_REGION="${AWS_REGION:-us-east-2}"
PROFILE="${AWS_PROFILE:-cert-ra-${ENV}-upgrader}"

read -r -d '' SQL <<'EOF' || true
SELECT ti.email,
       t.name        AS team_name,
       ti.created_at AS invited_at,
       ti.expires_at,
       ti.revoked_at,
       ti.accepted_at,
       ti.is_accepted,
       tm.created_at AS joined_team_at
FROM team_invitation ti
JOIN team t           ON t.id  = ti.team_id
JOIN user_account u   ON LOWER(u.email) = LOWER(ti.email)
JOIN team_member  tm  ON tm.user_id = u.id AND tm.team_id = ti.team_id
WHERE ti.is_accepted = false
  AND (ti.expires_at IS NULL OR ti.expires_at > now())
ORDER BY ti.created_at DESC;
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
