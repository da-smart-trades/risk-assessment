# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.dns.hosted_zone import (
    HostedZoneWithCert,
    HostedZoneWithCertProps,
)
from cert_ra_infra.stacks._config import EnvConfig


class DnsStack(Stack):
    """Foundation DNS — Route53 public hosted zone + ACM cert.

    Per the resource ownership matrix, this stack owns:
    - `HostedZoneWithCert` for the env's domain (from `EnvConfig.domain`)
    - ACM certificate with `*.<domain>` SAN, DNS-validated via this zone

    The hosted zone is authoritative for the env-specific subdomain
    (e.g. `cert-ra.staging.certora.com`). Initial setup includes a one-
    time out-of-band step: the operator copies the four NS records (a
    CFN output) into the parent zone's registrar/DNS console so DNS
    validation can complete.
    """

    dns: HostedZoneWithCert

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        self.dns = HostedZoneWithCert(
            self,
            "Dns",
            props=HostedZoneWithCertProps(
                domain_name=env_config.domain,
                hosted_zone_id=env_config.dns_zone_id,
            ),
        )

        cdk.CfnOutput(
            self,
            "HostedZoneId",
            value=self.dns.hosted_zone_id,
            export_name=f"{self.stack_name}-HostedZoneId",
        )
        cdk.CfnOutput(
            self,
            "DomainName",
            value=self.dns.hosted_zone_name,
            export_name=f"{self.stack_name}-DomainName",
        )
        cdk.CfnOutput(
            self,
            "CertificateArn",
            value=self.dns.certificate_arn,
            export_name=f"{self.stack_name}-CertificateArn",
        )
        # NS records are a token list — emit as a CSV string so the
        # operator-facing CFN console + describe-stacks output is readable.
        # CDK's hosted_zone_name_servers returns Optional[List[str]]; for the
        # public hosted zone case it's always populated, so the Fn.join
        # call below is safe at synth time.
        ns = self.dns.name_servers
        if ns is not None:
            cdk.CfnOutput(
                self,
                "NameServers",
                value=cdk.Fn.join(",", ns),
                description=(
                    "Delegate these NS records from the parent zone "
                    "(e.g. certora.com) before initial-setup completes."
                ),
                export_name=f"{self.stack_name}-NameServers",
            )
