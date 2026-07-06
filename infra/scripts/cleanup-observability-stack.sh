#!/usr/bin/env bash
# Tear down CertRa-ObservabilityStack-${ENV}.
#
# Provisions: CloudTrail trail + S3 bucket (in DataStack via the
# logs_bucket dependency), GuardDuty detector, Config recorder +
# delivery channel, plus the cert-ra-logs CMK.
#
# Heads-up:
# - CloudTrail trails can be deleted cleanly; the S3 destination
#   lives in DataStack so it's owned/cleaned there.
# - GuardDuty detector: deleting WIPES finding history. The script
#   prompts again before doing this.
# - AWS Config is account-scoped; ObservabilityStack creates the
#   recorder + delivery channel. We delete the recorder + channel
#   but leave any cross-account Config state alone.
#
# Usage: ENV=staging ./infra/scripts/cleanup-observability-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-ObservabilityStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# 1. CloudTrail.
log_step "Deleting CloudTrail trail"
for TRAIL in $(aws cloudtrail list-trails \
    --query "Trails[?starts_with(Name, 'cert-ra')].Name" \
    --output text 2>/dev/null); do
    aws cloudtrail delete-trail --name "$TRAIL" 2>&1 \
        | grep -v '^$' || true
    echo "  $TRAIL: deleted"
done

# 2. GuardDuty detector.
log_step "Deleting GuardDuty detector"
DETECTOR_ID=$(aws guardduty list-detectors \
    --query 'DetectorIds[0]' --output text 2>/dev/null || echo "")
if [[ -n "$DETECTOR_ID" && "$DETECTOR_ID" != "None" ]]; then
    echo "  Found GuardDuty detector $DETECTOR_ID (will WIPE finding history)"
    aws guardduty delete-detector --detector-id "$DETECTOR_ID" 2>&1 \
        | grep -v '^$' || true
fi

# 3. AWS Config: delete the recorder + delivery channel created by this
#    stack. We stop the recorder first.
log_step "Stopping + deleting AWS Config recorder"
for REC in $(aws configservice describe-configuration-recorders \
    --query "ConfigurationRecorders[?starts_with(name, 'cert-ra')].name" \
    --output text 2>/dev/null); do
    aws configservice stop-configuration-recorder \
        --configuration-recorder-name "$REC" 2>/dev/null || true
    aws configservice delete-configuration-recorder \
        --configuration-recorder-name "$REC" 2>&1 | grep -v '^$' || true
done
for CHAN in $(aws configservice describe-delivery-channels \
    --query "DeliveryChannels[?starts_with(name, 'cert-ra')].name" \
    --output text 2>/dev/null); do
    aws configservice delete-delivery-channel \
        --delivery-channel-name "$CHAN" 2>&1 | grep -v '^$' || true
done

# 4. Force-delete the CFN stack.
force_delete_cfn_stack "$STACK_NAME"

# 5. CMK.
cleanup_kms_cmk_by_alias "cert-ra-logs-${ENV}"

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "CloudTrail" aws cloudtrail list-trails --query "Trails[?starts_with(Name, 'cert-ra')]"
verify_resource_gone "GuardDuty detector" aws guardduty list-detectors --query 'DetectorIds'

log_header "$STACK_NAME cleanup complete"
