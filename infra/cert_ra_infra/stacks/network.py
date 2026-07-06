# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.network.public_alb import PublicAlb, PublicAlbProps
from cert_ra_infra.constructs.network.security_groups import (
    CertRaSecurityGroups,
    CertRaSecurityGroupsProps,
)
from cert_ra_infra.constructs.network.vpc import (
    VpcWithEndpoints,
    VpcWithEndpointsProps,
)
from cert_ra_infra.stacks._config import EnvConfig


class NetworkStack(Stack):
    """Foundation networking — VPC, subnets, NAT, VPC endpoints,
    per-role security groups, and the public ALB shell.

    Per the resource ownership matrix, this stack owns:
    - VPC + public/private-egress/private-isolated subnets
    - NAT gateways (1 per AZ in prod; 1 total in staging)
    - Interface + gateway VPC endpoints (with H2-A account-scope policies)
    - One security group per role (alb, app, worker, temporal_fe, maint,
      migrate, rds) — never shared between roles
    - The internet-facing ALB shell (no listeners yet)
    """

    vpc: VpcWithEndpoints
    security_groups: CertRaSecurityGroups
    public_alb: PublicAlb

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

        self.vpc = VpcWithEndpoints(
            self,
            "Vpc",
            props=VpcWithEndpointsProps(
                cidr=env_config.vpc_cidr,
                max_azs=env_config.max_azs,
                nat_gateways=env_config.nat_gateways,
            ),
        )

        self.security_groups = CertRaSecurityGroups(
            self,
            "SecurityGroups",
            props=CertRaSecurityGroupsProps(vpc=self.vpc.vpc),
        )

        self.public_alb = PublicAlb(
            self,
            "PublicAlb",
            props=PublicAlbProps(
                vpc=self.vpc.vpc,
                alb_security_group=self.security_groups.alb,
                alb_name=f"cert-ra-{env_config.env}",
            ),
        )

        # Outputs for downstream stacks and operator scripts. Service-construct
        # authors typically receive resources as props rather than re-importing,
        # but `upgrade.sh` reads these via `aws cloudformation describe-stacks`.
        cdk.CfnOutput(
            self,
            "VpcId",
            value=self.vpc.vpc.vpc_id,
            export_name=f"{self.stack_name}-VpcId",
        )
        cdk.CfnOutput(
            self,
            "PrivateEgressSubnetIds",
            value=cdk.Fn.join(
                ",", [s.subnet_id for s in self.vpc.private_egress_subnets]
            ),
            export_name=f"{self.stack_name}-PrivateEgressSubnetIds",
        )
        cdk.CfnOutput(
            self,
            "PrivateIsolatedSubnetIds",
            value=cdk.Fn.join(
                ",", [s.subnet_id for s in self.vpc.private_isolated_subnets]
            ),
            export_name=f"{self.stack_name}-PrivateIsolatedSubnetIds",
        )
        cdk.CfnOutput(
            self,
            "AlbArn",
            value=self.public_alb.alb_arn,
            export_name=f"{self.stack_name}-AlbArn",
        )
        cdk.CfnOutput(
            self,
            "AlbDnsName",
            value=self.public_alb.alb_dns_name,
            export_name=f"{self.stack_name}-AlbDnsName",
        )
