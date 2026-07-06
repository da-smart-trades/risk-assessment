# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""CloudFormation Custom Resource that disables the Temporal mTLS root
CA after the subordinate has been issued.

See § PCA structure (B2 resolved) in the design spec. The root CA is
only needed long enough to sign the subordinate CA's certificate. Once
the subordinate is active and issuing end-entity certs, the root CA's
job is done — keeping it ACTIVE would let an attacker who compromises
Installer + MFA issue rogue subordinates without re-enabling first.

Idempotent: re-runs are no-ops because UpdateCertificateAuthority with
the same status is a no-op. Re-enabling the root for subordinate
rotation is a manual runbook step (CDK deploy from Installer + MFA),
documented in `docs/temporal-mtls-rotation.md`.
"""

from __future__ import annotations

import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

acm_pca = boto3.client("acm-pca")


def handler(event: dict, _context: object) -> dict:
    """Custom Resource handler.

    Event shape:
        {
            "RequestType": "Create" | "Update" | "Delete",
            "ResourceProperties": {
                "RootCaArn": "..."
            }
        }
    """
    request_type = event["RequestType"]
    physical_id = event.get("PhysicalResourceId", "root-ca-disable")

    if request_type == "Delete":
        # Don't re-enable the root on stack delete — operator decision.
        return {"PhysicalResourceId": physical_id}

    if request_type == "Update":
        # No-op on update — disabling is one-shot.
        logger.info("Update event — no-op (root already disabled)")
        return {"PhysicalResourceId": physical_id}

    # Create path.
    props = event["ResourceProperties"]
    root_ca_arn = props["RootCaArn"]

    logger.info("Disabling root CA: %s", root_ca_arn)
    acm_pca.update_certificate_authority(
        CertificateAuthorityArn=root_ca_arn,
        Status="DISABLED",
    )
    logger.info("Root CA disabled")
    return {"PhysicalResourceId": "root-ca-disable"}
