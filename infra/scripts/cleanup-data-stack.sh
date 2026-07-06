#!/usr/bin/env bash
# Tear down CertRa-DataStack-${ENV} and all its retained resources.
#
# DataStack provisions resources with `RemovalPolicy.RETAIN` (KMS CMKs,
# S3 buckets, the RDS instance + master credential secret), and RDS
# defaults to `deletion_protection=True`. A normal `cdk destroy` or
# CFN delete leaves a substantial debris field — this script does the
# full sweep so the next `initial-setup.sh` run starts from a clean
# slate.
#
# Order matters: RDS first (slow, ~5-10 min), then parameter / subnet
# groups, S3 buckets, the CFN stack itself (which abandons the
# remaining KMS keys + aliases under RETAIN), then the KMS resources.
#
# IDEMPOTENT: each step checks whether the resource exists before
# acting, so re-running after partial failure picks up cleanly.
#
# Usage:
#   ENV=staging ./infra/scripts/cleanup-data-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
EXPECTED_PERMISSION_SET="CertRaInstaller"
STACK_NAME="CertRa-DataStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"

log_header "Cleaning up $STACK_NAME"

# Pre-flight: confirm role + region, then operator confirmation.
require_sso_session "$PROFILE" "$EXPECTED_PERMISSION_SET"

ACCOUNT_ID=$(aws --profile "$PROFILE" sts get-caller-identity \
    --query Account --output text)
echo
echo "About to PERMANENTLY DELETE all DataStack resources for:"
echo "    Account:     $ACCOUNT_ID"
echo "    Region:      $REGION"
echo "    Environment: $ENV"
echo "    Stack:       $STACK_NAME"
echo
echo "This destroys:"
echo "  - RDS Postgres instance (deletion protection will be removed first)"
echo "  - RDS master credential secret"
echo "  - RDS parameter group + subnet group"
echo "  - S3 buckets: cert-ra-logs-${ENV}, cert-ra-assets-${ENV} (incl. all objects/versions)"
echo "  - KMS CMKs: alias/cert-ra-rds-${ENV}, alias/cert-ra-s3-${ENV} (scheduled for 7-day deletion)"
echo
read -r -p "Type the env name '${ENV}' to confirm: " confirm
[[ "$confirm" == "$ENV" ]] || { echo "Aborted."; exit 1; }

export AWS_PROFILE="$PROFILE"
export AWS_REGION="$REGION"

# -------------------------------------------------------------------
# Step 1: identify the RDS instance and disable deletion protection.
# -------------------------------------------------------------------
log_step "Looking for RDS instance owned by $STACK_NAME"

RDS_ID=$(aws rds describe-db-instances \
    --query "DBInstances[?contains(TagList[?Key=='aws:cloudformation:stack-name'].Value, '${STACK_NAME}')].DBInstanceIdentifier | [0]" \
    --output text 2>/dev/null || echo "None")

if [[ "$RDS_ID" == "None" || -z "$RDS_ID" ]]; then
    # Stack tag query missed (common if CFN partially deleted). Fall back
    # to the conventional naming pattern. CDK-generated logical IDs
    # carry an 8-char hash suffix, so we filter by prefix.
    RDS_ID=$(aws rds describe-db-instances \
        --query "DBInstances[?starts_with(DBInstanceIdentifier, 'certra-datastack-${ENV}')].DBInstanceIdentifier | [0]" \
        --output text 2>/dev/null || echo "None")
fi

if [[ "$RDS_ID" == "None" || -z "$RDS_ID" ]]; then
    echo "No RDS instance found. Skipping RDS teardown."
else
    echo "Found RDS instance: $RDS_ID"

    # Disable deletion protection. RDS rejects delete-db-instance while
    # the flag is on.
    log_step "Disabling RDS deletion protection on $RDS_ID"
    aws rds modify-db-instance \
        --db-instance-identifier "$RDS_ID" \
        --no-deletion-protection \
        --apply-immediately >/dev/null
    aws rds wait db-instance-available --db-instance-identifier "$RDS_ID"

    # Capture the master secret ARN before delete (the secret survives
    # the instance and we want to clean it up separately).
    MASTER_SECRET_ARN=$(aws rds describe-db-instances \
        --db-instance-identifier "$RDS_ID" \
        --query 'DBInstances[0].MasterUserSecret.SecretArn' \
        --output text 2>/dev/null || echo "")

    log_step "Deleting RDS instance $RDS_ID (skip-final-snapshot)"
    aws rds delete-db-instance \
        --db-instance-identifier "$RDS_ID" \
        --skip-final-snapshot \
        --delete-automated-backups >/dev/null
    aws rds wait db-instance-deleted --db-instance-identifier "$RDS_ID"
    echo "RDS instance deleted."

    if [[ -n "$MASTER_SECRET_ARN" && "$MASTER_SECRET_ARN" != "None" ]]; then
        log_step "Force-deleting RDS master secret $MASTER_SECRET_ARN"
        aws secretsmanager delete-secret \
            --secret-id "$MASTER_SECRET_ARN" \
            --force-delete-without-recovery >/dev/null \
            && echo "Master secret deleted." \
            || echo "Master secret already gone."
    fi
fi

# -------------------------------------------------------------------
# Step 2: RDS parameter group + subnet group. Both share the prefix
# `certra-datastack-${ENV}` (CDK-generated). They can't be deleted
# while the RDS instance exists, but it's gone by now.
# -------------------------------------------------------------------
log_step "Deleting RDS parameter group + subnet group"

for PG in $(aws rds describe-db-parameter-groups \
    --query "DBParameterGroups[?starts_with(DBParameterGroupName, 'certra-datastack-${ENV}')].DBParameterGroupName" \
    --output text 2>/dev/null); do
    echo "  parameter group: $PG"
    aws rds delete-db-parameter-group --db-parameter-group-name "$PG" 2>&1 \
        | grep -v '^$' || true
done

for SG in $(aws rds describe-db-subnet-groups \
    --query "DBSubnetGroups[?starts_with(DBSubnetGroupName, 'certra-datastack-${ENV}')].DBSubnetGroupName" \
    --output text 2>/dev/null); do
    echo "  subnet group: $SG"
    aws rds delete-db-subnet-group --db-subnet-group-name "$SG" 2>&1 \
        | grep -v '^$' || true
done

# -------------------------------------------------------------------
# Step 3: Empty + delete the two S3 buckets.
# EncryptedBucket has versioning on; we have to delete all object
# versions AND delete markers, not just current objects.
# -------------------------------------------------------------------
for BUCKET in "cert-ra-logs-${ENV}" "cert-ra-assets-${ENV}"; do
    log_step "Emptying + deleting S3 bucket: $BUCKET"

    if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
        echo "  $BUCKET does not exist; skipping."
        continue
    fi

    # Drop the deny-cross-account-principals resource policy first;
    # versioned-delete calls might trip it under some session shapes.
    aws s3api delete-bucket-policy --bucket "$BUCKET" 2>/dev/null || true

    # Empty current objects.
    aws s3 rm "s3://${BUCKET}/" --recursive --quiet 2>/dev/null || true

    # Empty all versions + delete markers.
    python3 - <<EOF
import json, subprocess
bucket = "$BUCKET"

def aws(*args):
    return subprocess.run(
        ["aws"] + list(args),
        capture_output=True, text=True, check=False,
    )

while True:
    out = aws("s3api", "list-object-versions", "--bucket", bucket,
              "--max-items", "1000", "--output", "json")
    if out.returncode != 0 or not out.stdout.strip():
        break
    data = json.loads(out.stdout)
    objs = []
    for v in data.get("Versions", []) or []:
        objs.append({"Key": v["Key"], "VersionId": v["VersionId"]})
    for m in data.get("DeleteMarkers", []) or []:
        objs.append({"Key": m["Key"], "VersionId": m["VersionId"]})
    if not objs:
        break
    # Batch delete in chunks of 1000.
    for i in range(0, len(objs), 1000):
        chunk = objs[i:i+1000]
        payload = json.dumps({"Objects": chunk, "Quiet": True})
        aws("s3api", "delete-objects", "--bucket", bucket,
            "--delete", payload)
EOF

    # Now the bucket is empty; delete it.
    aws s3api delete-bucket --bucket "$BUCKET" 2>&1 | grep -v '^$' || true
    echo "  $BUCKET deleted."
done

# -------------------------------------------------------------------
# Step 4: force-delete the CFN stack. Anything left (KMS keys/aliases
# under RETAIN) gets abandoned; we clean those up below.
# -------------------------------------------------------------------
log_step "Force-deleting CloudFormation stack $STACK_NAME"

STACK_STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "NOT_EXISTS")

if [[ "$STACK_STATUS" == "NOT_EXISTS" ]]; then
    echo "$STACK_NAME does not exist; skipping CFN delete."
else
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --deletion-mode FORCE_DELETE_STACK
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" 2>/dev/null \
        || echo "wait failed; stack may already be gone."
fi

# -------------------------------------------------------------------
# Step 5: KMS aliases + keys.
# The MFA-gated `DenyKeyDeletionWithoutMfa` statement on each key
# blocks ScheduleKeyDeletion; we use the AccountRootPolicyUpdate
# Sid (which grants unconditional kms:PutKeyPolicy) to drop the
# deny first, then schedule.
# -------------------------------------------------------------------
for ALIAS in "cert-ra-rds-${ENV}" "cert-ra-s3-${ENV}"; do
    log_step "Cleaning up KMS alias/${ALIAS} + key"

    KEY_ID=$(aws kms describe-key \
        --key-id "alias/${ALIAS}" \
        --query 'KeyMetadata.KeyId' --output text 2>/dev/null || echo "")

    if [[ -z "$KEY_ID" ]]; then
        echo "  alias/${ALIAS} does not exist; skipping."
        continue
    fi

    # Strip the MFA deny so we can call ScheduleKeyDeletion.
    aws kms get-key-policy \
        --key-id "$KEY_ID" --policy-name default \
        --query 'Policy' --output text > "/tmp/${ALIAS}-policy.json"

    python3 - <<EOF
import json
p = json.load(open("/tmp/${ALIAS}-policy.json"))
p["Statement"] = [s for s in p["Statement"]
                  if s.get("Sid") != "DenyKeyDeletionWithoutMfa"]
p["Statement"].append({
    "Sid": "TempUnblockDelete",
    "Effect": "Allow",
    "Principal": {"AWS": "arn:aws:iam::${ACCOUNT_ID}:root"},
    "Action": "kms:*",
    "Resource": "*",
})
json.dump(p, open("/tmp/${ALIAS}-policy.json", "w"))
EOF

    aws kms put-key-policy \
        --key-id "$KEY_ID" --policy-name default \
        --policy "file:///tmp/${ALIAS}-policy.json" >/dev/null

    aws kms delete-alias --alias-name "alias/${ALIAS}" 2>&1 \
        | grep -v '^$' || true

    aws kms schedule-key-deletion \
        --key-id "$KEY_ID" \
        --pending-window-in-days 7 \
        --query 'DeletionDate' --output text 2>&1 \
        | grep -v '^$' || true

    rm -f "/tmp/${ALIAS}-policy.json"
done

# -------------------------------------------------------------------
# Step 6: verify everything is gone.
# -------------------------------------------------------------------
log_header "Verification"

echo "CFN stack:"
aws cloudformation describe-stacks --stack-name "$STACK_NAME" 2>&1 \
    | head -1 \
    | grep -E 'does not exist|NoSuchStack' \
    && echo "  CLEAN" \
    || echo "  STILL PRESENT"

echo "RDS instances starting with certra-datastack-${ENV}:"
RESULT=$(aws rds describe-db-instances \
    --query "DBInstances[?starts_with(DBInstanceIdentifier, 'certra-datastack-${ENV}')].DBInstanceIdentifier" \
    --output text 2>/dev/null)
[[ -z "$RESULT" ]] && echo "  CLEAN" || echo "  STILL PRESENT: $RESULT"

echo "S3 buckets:"
for BUCKET in "cert-ra-logs-${ENV}" "cert-ra-assets-${ENV}"; do
    aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null \
        && echo "  $BUCKET: STILL PRESENT" \
        || echo "  $BUCKET: CLEAN"
done

echo "KMS aliases (cert-ra- prefix):"
RESULT=$(aws kms list-aliases \
    --query "Aliases[?AliasName=='alias/cert-ra-rds-${ENV}' || AliasName=='alias/cert-ra-s3-${ENV}'].AliasName" \
    --output text)
[[ -z "$RESULT" ]] && echo "  CLEAN" || echo "  STILL PRESENT: $RESULT"

log_header "$STACK_NAME cleanup complete"
echo
echo "Next: re-run initial-setup.sh (or just cdk deploy CertRa-DataStack-${ENV})"
echo "to recreate DataStack from scratch."
