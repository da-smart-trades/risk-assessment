# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from cdk_nag import NagSuppressions
from constructs import Construct


@dataclass(frozen=True, slots=True)
class PublicAlbProps:
    """Props for PublicAlb.

    See § Resource ownership matrix — `NetworkStack` provisions the ALB
    shell; `LitestarService` adds listeners + target groups.
    """

    vpc: ec2.IVpc
    alb_security_group: ec2.ISecurityGroup
    alb_name: str = "cert-ra-alb"


class PublicAlb(Construct):
    """Internet-facing Application Load Balancer.

    A shell only — no listeners or target groups. Listeners are added by
    `LitestarService` (one production listener on :443 + one test listener
    on :8443 for the blue/green BeforeAllowTraffic hook). Decoupling the
    ALB from its listeners lets us swap in CodeDeploy-managed
    listener-default-action shifts without churning the ALB itself.

    Access logs are deferred until `BaselineCloudTrail` (ObservabilityStack)
    creates the `cert-ra-logs-{env}` S3 bucket — circular-import-avoidance.
    cdk-nag flags missing access logs; suppressed here with that pointer.
    """

    alb: elbv2.ApplicationLoadBalancer

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: PublicAlbProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "Alb",
            vpc=props.vpc,
            internet_facing=True,
            load_balancer_name=props.alb_name,
            security_group=props.alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            drop_invalid_header_fields=True,
            deletion_protection=True,
        )

        NagSuppressions.add_resource_suppressions(
            self.alb,
            [
                {
                    "id": "AwsSolutions-ELB2",
                    "reason": (
                        "TODO: enable access logs once cert-ra-logs-${env} S3 bucket "
                        "exists in DataStack. Tracked under L2 (tamper-resistant trail) "
                        "in the security hardening backlog."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-ELBLoggingEnabled",
                    "reason": "Same as AwsSolutions-ELB2: deferred to DataStack landing.",
                },
                {
                    "id": "NIST.800.53.R5-ALBWAFEnabled",
                    "reason": (
                        "WAFv2 deferred to a future hardening PR. The OIDC + session-"
                        "secret auth path already restricts unauthenticated routes; WAF "
                        "is a defence-in-depth layer that will be added under a follow-"
                        "up L-series backlog item."
                    ),
                },
            ],
        )

    @property
    def alb_arn(self) -> str:
        return self.alb.load_balancer_arn

    @property
    def alb_dns_name(self) -> str:
        return self.alb.load_balancer_dns_name

    @property
    def alb_hosted_zone_id(self) -> str:
        return self.alb.load_balancer_canonical_hosted_zone_id
