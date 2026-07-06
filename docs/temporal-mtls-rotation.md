# Temporal mTLS Rotation

How the cert family that authenticates every Temporal connection is
rotated. Two layers:

1. **End-entity certs** — the five `*.cert-ra.local` certs (one
   server cert + four client certs). Rotated **automatically daily**
   by `CertRenewal`, no operator action required.
2. **Subordinate CA** — the issuing CA itself. Rotated **manually**
   on a slow cadence (annual or on compromise). Requires re-enabling
   the root CA, which is gated by the Installer permission set + MFA.

The root CA stays `DISABLED` between rotations: its only job is to sign
subordinate CAs, and disabling it limits the blast radius if the
Installer role is ever compromised.

---

## End-entity cert rotation (automatic)

### Cert lifetime + renewal trigger

| Property | Value |
| --- | --- |
| Validity | 13 months (397 days) |
| Renewal threshold | 159 days remaining (~40% of lifetime) |
| Renewal frequency | Daily check at 02:00 UTC |

`CertRenewal` is an EventBridge-scheduled Lambda (`scheduled.cert_renewal`).
Each daily run:

1. Reads each of the five SeededSecret entries
   (`/cert-ra/${env}/temporal/mtls/{temporal-frontend,worker-metrics,
   worker-alerts,internal-worker,maint}`).
2. Parses the PEM cert and computes days-to-expiry.
3. If ≥ 159 days remaining, no-ops.
4. Otherwise:
   - Generates a new EC P-256 keypair.
   - Builds a CSR with the cert's CommonName.
   - Calls `acm-pca:IssueCertificate` against the subordinate CA.
   - Polls until `ISSUED` (typically 30-90 s).
   - Writes the new `{cert, chain, key}` JSON payload to the
     SeededSecret, overwriting the prior `AWSCURRENT`.
5. ECS tasks pick up the new cert at next restart. Deploy cadence
   (multiple/week in steady state) naturally cycles tasks well
   within the renewal window.

### Verifying renewal succeeded

```bash
# Days remaining on each cert
for svc in temporal-frontend worker-metrics worker-alerts internal-worker maint; do
    aws secretsmanager get-secret-value \
        --secret-id "/cert-ra/${env}/temporal/mtls/${svc}" \
        --query 'SecretString' --output text \
        | jq -r '.cert' \
        | openssl x509 -noout -enddate \
        | sed "s/^/${svc}: /"
done
```

CertRenewal also writes to CloudWatch Logs at
`/aws/lambda/CertRa-TemporalStack-${env}-CertRenewal*`. Tail with:

```bash
aws logs tail "/aws/lambda/CertRa-TemporalStack-${env}-CertRenewal" \
    --follow --filter-pattern 'renewed OR ISSUED OR FAILED'
```

### When automatic renewal fails

Common causes:
- Subordinate CA was disabled out of band (CertRenewal's `IssueCertificate`
  call fails).
- KMS Decrypt permissions on the secrets CMK were revoked.
- The PEM payload in the SeededSecret was hand-edited and no longer
  parses.

Recovery: run `CertRenewal` manually with a forced update:

```bash
RENEWAL_FN=$(aws cloudformation describe-stacks \
    --stack-name CertRa-TemporalStack-${env} \
    --query 'Stacks[0].Outputs[?OutputKey==`CertRenewalHandlerArn`].OutputValue' \
    --output text)
aws lambda invoke \
    --function-name "$RENEWAL_FN" \
    --payload '{"force": true}' \
    /tmp/renewal-out.json
cat /tmp/renewal-out.json
```

If a single cert is broken (e.g. operator hand-edited it), force-rotate
just that one via `InitialCertIssuance`'s Custom Resource by deleting +
recreating its CDK construct — but that's invasive enough to be a
last resort.

---

## Subordinate CA rotation (manual, annual)

The subordinate CA's own cert is valid for **10 years** by default
(ACM PCA's `AWSPrivateCAStrictMode` template). We rotate **annually**
to limit how long any one signing key is in use, and **immediately on
suspected compromise**.

### Prerequisites

- `CertRaInstaller` permission set + valid MFA token.
- Operator with KMS key-policy understanding (the `cert-ra-temporal-ca-cmk`
  policy gates `acm-pca` operations).
- A maintenance window — workers will need to be restarted to pick up
  the new trust bundle.

### Procedure

1. **Re-enable the root CA.** The `RootCaDisable` Custom Resource
   left the root in `DISABLED` after initial subordinate issuance.
   Re-enable via the AWS Console (CloudTrail-visible; MFA-gated by
   the KMS CMK policy) **or** by temporarily commenting out the
   `RootCaDisable` construct in TemporalStack and re-deploying.

   ```bash
   aws acm-pca update-certificate-authority \
       --certificate-authority-arn "$ROOT_CA_ARN" \
       --status ACTIVE
   ```

2. **Issue a new subordinate CA cert.** Generate a CSR for a new
   subordinate, sign it with the root, and import it into a fresh
   subordinate CA resource:

   ```bash
   # New subordinate CA (different name suffix so the old one stays
   # around long enough for clients to pick up the new chain).
   aws acm-pca create-certificate-authority \
       --certificate-authority-configuration file://subordinate-config.json \
       --certificate-authority-type SUBORDINATE \
       --revocation-configuration file://revocation-config.json \
       --tags Key=Name,Value=cert-ra-temporal-ca-${env}-v2

   # CSR from the new subordinate.
   NEW_SUB_ARN=…
   aws acm-pca get-certificate-authority-csr \
       --certificate-authority-arn "$NEW_SUB_ARN" \
       --output text > /tmp/sub-v2.csr

   # Sign with the root.
   aws acm-pca issue-certificate \
       --certificate-authority-arn "$ROOT_CA_ARN" \
       --csr fileb:///tmp/sub-v2.csr \
       --signing-algorithm SHA256WITHECDSA \
       --template-arn arn:aws:acm-pca:::template/SubordinateCACertificate_PathLen0/V1 \
       --validity Value=120,Type=MONTHS

   # Wait for issuance, fetch the cert, import into the new subordinate.
   ```

3. **Re-issue every end-entity cert** under the new subordinate.
   Invoke `InitialCertIssuance` against the new subordinate ARN, or
   force-renew via the CertRenewal Lambda with `--force` after
   pointing it at the new subordinate (update the `subordinate_ca_arn`
   prop in TemporalStack and re-deploy).

4. **Switch all consumers to the new trust bundle.** The cert/chain
   JSON payload in each SeededSecret now contains both the
   end-entity cert AND the new subordinate's chain. Rolling-restart
   every ECS service in TemporalStack + WorkersStack + AppStack +
   MaintenanceStack so they reload the new chain:

   ```bash
   for svc in cert-ra-app-${env} cert-ra-worker-metrics-${env} cert-ra-worker-alerts-${env} cert-ra-maint-${env}; do
       aws ecs update-service \
           --cluster "$(echo "$svc" | sed 's/-${env}$/-cluster/')" \
           --service "$svc" \
           --force-new-deployment
   done
   ```

5. **Disable the old subordinate CA** once every consumer has been
   verified on the new chain.

6. **Re-disable the root CA** (the long-term safe state):

   ```bash
   aws acm-pca update-certificate-authority \
       --certificate-authority-arn "$ROOT_CA_ARN" \
       --status DISABLED
   ```

   Or, if the `RootCaDisable` construct was commented out, restore it
   and re-deploy TemporalStack — the Custom Resource is idempotent
   and will disable on re-create.

### Verifying the new subordinate

```bash
# The end-entity cert's issuer should now show the new subordinate's
# CommonName / serial.
aws secretsmanager get-secret-value \
    --secret-id "/cert-ra/${env}/temporal/mtls/temporal-frontend" \
    --query 'SecretString' --output text \
    | jq -r '.cert' \
    | openssl x509 -noout -issuer
```

### Roll-back plan

Before disabling the old subordinate (step 5), the old chain is still
valid. To revert mid-rotation:

- Re-issue the SeededSecret payloads pointing back at the old
  subordinate (run `InitialCertIssuance` against `OLD_SUB_ARN`).
- Force-restart every consumer.
- Disable the new subordinate.

---

## What this does NOT cover

- **CA private-key compromise** is out of scope. ACM PCA stores the
  key in a hardware boundary; this runbook relies on ACM PCA's
  security guarantees.
- **CRL distribution + revocation**. Out of scope for the initial
  cert-ra deploy. End-entity revocation today is via re-issuance +
  service restart; CRL-driven revocation is a follow-up
  ObservabilityStack item.

---

## References

- `TemporalMtlsPki` construct:
  [`infra/cert_ra_infra/constructs/temporal/mtls_pki.py`](../infra/cert_ra_infra/constructs/temporal/mtls_pki.py)
- `CertRenewal` Lambda:
  [`infra/cert_ra_infra/constructs/temporal/cert_renewal.py`](../infra/cert_ra_infra/constructs/temporal/cert_renewal.py)
- `RootCaDisable` Custom Resource:
  [`infra/cert_ra_infra/constructs/temporal/root_ca_disable.py`](../infra/cert_ra_infra/constructs/temporal/root_ca_disable.py)
