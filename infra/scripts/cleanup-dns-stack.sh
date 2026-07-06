#!/usr/bin/env bash
# Tear down CertRa-DnsStack-${ENV}: Route53 hosted zone + ACM cert.
#
# Route53 zones must be empty (no records besides NS + SOA) before
# delete. AppStack owns the apex + www A-records; cleanup-app-stack.sh
# should remove those, but we sweep any leftovers here as a fallback.
#
# ACM certs can't be deleted while attached to a listener / CloudFront
# / etc. — AppStack's listeners should already be gone before this
# runs.
#
# Usage: ENV=staging ./infra/scripts/cleanup-dns-stack.sh

set -euo pipefail

ENV="${ENV:-staging}"
PROFILE="cert-ra-${ENV}-installer"
STACK_NAME="CertRa-DnsStack-${ENV}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/_common.sh"
REGION="$(resolve_region "$ENV")"
source "$SCRIPT_DIR/_cleanup_common.sh"

log_header "Cleaning up $STACK_NAME"
confirm_cleanup_intent "$STACK_NAME"

# Per-env domain from the deployment config (see deployment.config.example.json).
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG="$REPO_ROOT/deployment.config.json"
[[ -f "$CONFIG" ]] || CONFIG="$REPO_ROOT/deployment.config.example.json"
DOMAIN="$(jq -r ".environments.\"${ENV}\".domain" "$CONFIG")"

# 1. Empty the hosted zone (drop A-records etc., leave NS + SOA).
log_step "Emptying Route53 zone $DOMAIN"
ZONE_ID=$(aws route53 list-hosted-zones-by-name \
    --dns-name "$DOMAIN" \
    --query "HostedZones[?Name=='${DOMAIN}.'].Id | [0]" \
    --output text 2>/dev/null | sed 's|/hostedzone/||' || echo "")

if [[ -n "$ZONE_ID" && "$ZONE_ID" != "None" ]]; then
    # Build a change-batch JSON that deletes everything except NS + SOA.
    aws route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" \
        --output json > /tmp/dns-records.json
    python3 - <<EOF
import json
data = json.load(open("/tmp/dns-records.json"))
changes = []
for rs in data.get("ResourceRecordSets", []):
    if rs["Type"] in ("NS", "SOA") and rs["Name"] == "${DOMAIN}.":
        continue
    changes.append({"Action": "DELETE", "ResourceRecordSet": rs})
if changes:
    out = {"Comment": "cleanup-dns-stack.sh", "Changes": changes}
    json.dump(out, open("/tmp/dns-changes.json", "w"))
    print(len(changes))
else:
    print(0)
EOF

    if [[ -f /tmp/dns-changes.json ]]; then
        echo "  Deleting non-NS/SOA records"
        aws route53 change-resource-record-sets \
            --hosted-zone-id "$ZONE_ID" \
            --change-batch file:///tmp/dns-changes.json \
            --query 'ChangeInfo.Status' --output text \
            | grep -v '^$' || true
        rm -f /tmp/dns-changes.json
    fi
    rm -f /tmp/dns-records.json
fi

# 2. ACM cert. Look up by domain name; delete if it's not in use.
log_step "Deleting ACM cert for $DOMAIN"
CERT_ARN=$(aws acm list-certificates \
    --query "CertificateSummaryList[?DomainName=='${DOMAIN}'].CertificateArn | [0]" \
    --output text 2>/dev/null || echo "None")

if [[ -n "$CERT_ARN" && "$CERT_ARN" != "None" ]]; then
    aws acm delete-certificate --certificate-arn "$CERT_ARN" 2>&1 \
        | grep -v '^$' \
        || echo "  ACM cert delete failed (likely still attached to a listener — remove dependents first)"
fi

# 3. Force-delete the CFN stack.
force_delete_cfn_stack "$STACK_NAME"

# 4. If the hosted zone survived (it's normally part of the stack),
#    delete it directly. Must be empty.
if [[ -n "$ZONE_ID" && "$ZONE_ID" != "None" ]]; then
    log_step "Deleting Route53 hosted zone $ZONE_ID"
    aws route53 delete-hosted-zone --id "$ZONE_ID" 2>&1 \
        | grep -v '^$' || true
fi

log_header "Verification"
verify_resource_gone "CFN stack" aws cloudformation describe-stacks --stack-name "$STACK_NAME"
verify_resource_gone "Hosted zone" aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" --query "HostedZones[?Name=='${DOMAIN}.']"
verify_resource_gone "ACM cert" aws acm list-certificates --query "CertificateSummaryList[?DomainName=='${DOMAIN}']"

log_header "$STACK_NAME cleanup complete"
echo
echo "Reminder: the Cloudflare NS delegation for ${DOMAIN} still points"
echo "at the deleted Route53 zone's nameservers. After re-running"
echo "initial-setup.sh, CertRa-DnsStack will emit FOUR NEW NS records;"
echo "you'll need to update Cloudflare to delegate to those instead."
