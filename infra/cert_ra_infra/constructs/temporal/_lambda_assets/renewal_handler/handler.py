# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""EventBridge-scheduled handler that renews Temporal mTLS end-entity certs.

See § Cert rotation (B1 path 2) in the design spec. Runs daily; for each
service, it reads the current cert from Secrets Manager, parses the
expiration date, and re-issues the cert (new key + new cert) if less
than `RenewWhenDaysRemaining` days remain.

Re-issuance uses the same `acm-pca:IssueCertificate` flow as
InitialCertIssuance (B1 path 1). Workers pick up the new cert at next
task restart (deploy or autoscaling event). At the project's deploy
cadence, tasks naturally cycle well within a cert's validity window.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime

import boto3
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger()
logger.setLevel(logging.INFO)

acm_pca = boto3.client("acm-pca")
secrets = boto3.client("secretsmanager")

END_ENTITY_TEMPLATE_ARN = "arn:aws:acm-pca:::template/EndEntityCertificate/V1"
SIGNING_ALGORITHM = "SHA256WITHECDSA"
DEFAULT_VALIDITY_DAYS = 397  # ~13 months (A6)
DEFAULT_RENEW_WHEN_DAYS_REMAINING = (
    159  # ~40% of 397 — matches ACM PCA's 60% renewal point
)
POLL_INTERVAL_SECONDS = 3
POLL_MAX_ATTEMPTS = 200


def handler(event: dict, _context: object) -> dict:
    """Scheduled EventBridge target.

    Event shape (passed via EventBridge rule input):
        {
            "SubordinateCaArn": "...",
            "RenewWhenDaysRemaining": 159,
            "Services": [
                {
                    "Name": "temporal-frontend",
                    "SecretName": "/cert-ra/staging/temporal/mtls/temporal-frontend",
                    "CommonName": "temporal-frontend.cert-ra.local",
                    "ValidityDays": 397
                },
                ...
            ]
        }
    """
    ca_arn = event["SubordinateCaArn"]
    services = event["Services"]
    threshold_days = int(
        event.get("RenewWhenDaysRemaining", DEFAULT_RENEW_WHEN_DAYS_REMAINING)
    )

    renewed: list[str] = []
    skipped: list[str] = []

    for service in services:
        days_remaining = _days_until_cert_expiry(secret_name=service["SecretName"])
        if days_remaining is None or days_remaining < threshold_days:
            logger.info(
                "Renewing %s (days_remaining=%s, threshold=%d)",
                service["Name"],
                days_remaining,
                threshold_days,
            )
            _issue_and_store(ca_arn=ca_arn, service=service)
            renewed.append(service["Name"])
        else:
            logger.info(
                "Skipping %s (days_remaining=%d, threshold=%d)",
                service["Name"],
                days_remaining,
                threshold_days,
            )
            skipped.append(service["Name"])

    return {"renewed": renewed, "skipped": skipped}


def _days_until_cert_expiry(*, secret_name: str) -> int | None:
    """Returns days until the cert in this secret expires, or None if the
    secret holds the `__SEED_ME__` placeholder or is otherwise unreadable
    (meaning: re-issue ASAP).
    """
    try:
        secret_value = secrets.get_secret_value(SecretId=secret_name)["SecretString"]
    except secrets.exceptions.ResourceNotFoundException:
        return None

    try:
        payload = json.loads(secret_value)
    except (ValueError, TypeError):
        # Placeholder string or other non-JSON content — treat as needing renewal.
        return None

    cert_pem = payload.get("cert")
    if not cert_pem:
        return None

    cert = x509.load_pem_x509_certificate(cert_pem.encode("utf-8"))
    delta = cert.not_valid_after_utc - datetime.now(UTC)
    return delta.days


def _issue_and_store(*, ca_arn: str, service: dict) -> None:
    """Generate a fresh key + CSR, request a new cert from PCA, overwrite the secret."""
    name = service["Name"]
    secret_name = service["SecretName"]
    common_name = service["CommonName"]
    validity_days = int(service.get("ValidityDays", DEFAULT_VALIDITY_DAYS))

    private_key = ec.generate_private_key(ec.SECP256R1())

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(common_name)]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)

    response = acm_pca.issue_certificate(
        CertificateAuthorityArn=ca_arn,
        Csr=csr_pem,
        SigningAlgorithm=SIGNING_ALGORITHM,
        TemplateArn=END_ENTITY_TEMPLATE_ARN,
        Validity={"Type": "DAYS", "Value": validity_days},
    )
    cert_arn = response["CertificateArn"]

    certificate_pem, chain_pem = _poll_for_certificate(
        ca_arn=ca_arn, cert_arn=cert_arn, service_name=name
    )

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    payload = {
        "cert": certificate_pem,
        "chain": chain_pem,
        "key": key_pem,
    }

    # PutSecretValue overwrites the AWSCURRENT version; the prior version
    # becomes AWSPREVIOUS automatically. Operators can roll back via
    # `secretsmanager:UpdateSecretVersionStage` if a renewal goes bad.
    secrets.put_secret_value(
        SecretId=secret_name,
        SecretString=json.dumps(payload),
    )
    logger.info("Renewed cert for %s", name)


def _poll_for_certificate(
    *, ca_arn: str, cert_arn: str, service_name: str
) -> tuple[str, str]:
    for _attempt in range(POLL_MAX_ATTEMPTS):
        try:
            cert_response = acm_pca.get_certificate(
                CertificateAuthorityArn=ca_arn,
                CertificateArn=cert_arn,
            )
            return cert_response["Certificate"], cert_response["CertificateChain"]
        except acm_pca.exceptions.RequestInProgressException:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

    raise TimeoutError(
        f"PCA did not issue cert for {service_name} after "
        f"{POLL_MAX_ATTEMPTS * POLL_INTERVAL_SECONDS} seconds"
    )
