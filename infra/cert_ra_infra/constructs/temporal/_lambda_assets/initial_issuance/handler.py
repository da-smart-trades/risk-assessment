# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""CloudFormation Custom Resource: issue end-entity certs from a
subordinate ACM Private CA and write them into Secrets Manager.

See § Initial cert population — synchronous Custom Resource (B1) in the
design spec.

Behaviour by `RequestType`:

  - `Create`: issue a cert for every service in the input list and store
    each in the corresponding Secrets Manager entry.
  - `Update`: issue a cert for any service whose secret is *still empty*
    (placeholder or no `cert` field). Services with existing certs are
    skipped — those are handled by the daily renewal Lambda. This lets
    operators add a new entry to `_MTLS_SERVICE_NAMES` and have the
    cert appear on the next `cdk deploy` without a manual renewal-
    Lambda invocation. Re-running deploys without service-list changes
    is a no-op because every secret is already populated.
  - `Delete`: no-op (secrets are `RemovalPolicy.RETAIN`).
"""

from __future__ import annotations

import json
import logging
import time

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
POLL_INTERVAL_SECONDS = 3
POLL_MAX_ATTEMPTS = 200  # ~10 minutes; CA issuance is usually 30-90s


def handler(event: dict, _context: object) -> dict:
    """Custom Resource handler.

    Event shape (CFN Custom Resource):
        {
            "RequestType": "Create" | "Update" | "Delete",
            "ResourceProperties": {
                "SubordinateCaArn": "...",
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
        }
    """
    request_type = event["RequestType"]
    physical_id = event.get("PhysicalResourceId", "initial-cert-issuance")

    if request_type == "Delete":
        # Secrets are RemovalPolicy.RETAIN; don't touch them on stack delete.
        return {"PhysicalResourceId": physical_id}

    props = event["ResourceProperties"]
    ca_arn = props["SubordinateCaArn"]
    services = props["Services"]

    if request_type == "Update":
        # Mint certs only for services that don't have one yet — typically
        # because a new entry was added to `_MTLS_SERVICE_NAMES` between
        # deploys and the corresponding SeededSecret is still on its
        # placeholder. Services with real cert payloads are left alone;
        # the daily renewal Lambda handles rotation.
        empty_services = [s for s in services if _is_unpopulated(s["SecretName"])]
        logger.info(
            "Update event — issuing for %d/%d services (others already populated)",
            len(empty_services),
            len(services),
        )
        for service in empty_services:
            _issue_and_store(ca_arn=ca_arn, service=service)
        return {"PhysicalResourceId": physical_id}

    # Create path — fresh stack, no secrets populated yet.
    for service in services:
        _issue_and_store(ca_arn=ca_arn, service=service)
    return {"PhysicalResourceId": "initial-cert-issuance"}


def _is_unpopulated(secret_name: str) -> bool:
    """Return True if the secret has no usable cert payload.

    Treats the SeededSecret's placeholder string, an empty value, a
    missing secret, and any payload that doesn't parse as JSON with a
    non-empty `cert` field as "unpopulated". Matches the renewal
    handler's `_days_until_cert_expiry` parsing so the two stay in
    lock-step on what counts as "needs a fresh cert".
    """
    try:
        secret_value = secrets.get_secret_value(SecretId=secret_name)["SecretString"]
    except secrets.exceptions.ResourceNotFoundException:
        return True
    try:
        payload = json.loads(secret_value)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    return not payload.get("cert")


def _issue_and_store(*, ca_arn: str, service: dict) -> None:
    """Generate a key + CSR, request a cert from PCA, store in Secrets Manager."""
    name = service["Name"]
    secret_name = service["SecretName"]
    common_name = service["CommonName"]
    validity_days = int(service.get("ValidityDays", DEFAULT_VALIDITY_DAYS))

    logger.info("Issuing cert for %s (CN=%s)", name, common_name)

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
    logger.info("PCA accepted CSR for %s; cert_arn=%s", name, cert_arn)

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

    secrets.put_secret_value(
        SecretId=secret_name,
        SecretString=json.dumps(payload),
    )
    logger.info("Stored cert for %s in %s", name, secret_name)


def _poll_for_certificate(
    *, ca_arn: str, cert_arn: str, service_name: str
) -> tuple[str, str]:
    """Block until ACM PCA finishes issuing the cert. Returns (cert_pem, chain_pem)."""
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
