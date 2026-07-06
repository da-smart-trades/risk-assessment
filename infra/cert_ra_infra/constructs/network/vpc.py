# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from cdk_nag import NagSuppressions
from constructs import Construct


@dataclass(frozen=True, slots=True)
class VpcWithEndpointsProps:
    """Props for VpcWithEndpoints. See § Resource ownership matrix in the
    design spec — NetworkStack owns the VPC, subnets, NAT, and interface
    endpoints; other stacks reference them."""

    cidr: str
    """e.g. `10.0.0.0/16`. Different per env to allow future VPC peering."""

    max_azs: int = 3
    """Number of Availability Zones to span. 3 for prod (HA); 2 acceptable for staging."""

    nat_gateways: int = 3
    """Number of NAT gateways. 1-per-AZ for prod (HA); 1 total for staging (cost)."""

    flow_logs_retention_days: int = 30
    """VPC Flow Logs retention. H2-C uses these for maint REJECT alarming."""


# Interface VPC endpoints required by the cert-ra services. Format: AWS service
# enum from `ec2.InterfaceVpcEndpointAwsService`. See § Maintenance container
# H2-A and the resource ownership matrix.
_INTERFACE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("EcrApi", "ECR"),
    ("EcrDkr", "ECR_DOCKER"),
    ("SecretsManager", "SECRETS_MANAGER"),
    ("Kms", "KMS"),
    ("CloudWatchLogs", "CLOUDWATCH_LOGS"),
    ("Sts", "STS"),
    ("SsmMessages", "SSM_MESSAGES"),
    ("Ec2Messages", "EC2_MESSAGES"),
    ("Ssm", "SSM"),
    ("CodeDeploy", "CODEDEPLOY"),
    ("CodeDeployAgent", "CODEDEPLOY_COMMANDS_SECURE"),
)


class VpcWithEndpoints(Construct):
    """VPC with public + private-egress + private-isolated subnets, NAT, and
    a complete set of interface + gateway VPC endpoints for AWS services.

    **Subnet topology** (per AZ):
    - Public: ALB + NAT gateway
    - Private (egress): ECS tasks for app, workers, maint, migrate
    - Private (isolated): RDS, Temporal persistence

    **Interface endpoints** (H2-A) all carry a policy restricting reachable
    resources to `aws:ResourceAccount = <this account>`. That makes
    cross-account exfil through the endpoint fail closed.

    **Flow Logs** captured to CloudWatch (default 30-day retention) for H2-C
    REJECT alarming on the maint task ENIs.
    """

    vpc: ec2.Vpc
    public_subnets: list[ec2.ISubnet]
    private_egress_subnets: list[ec2.ISubnet]
    private_isolated_subnets: list[ec2.ISubnet]
    interface_endpoints: dict[str, ec2.InterfaceVpcEndpoint]
    s3_gateway_endpoint: ec2.GatewayVpcEndpoint
    dynamodb_gateway_endpoint: ec2.GatewayVpcEndpoint

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: VpcWithEndpointsProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(props.cidr),
            max_azs=props.max_azs,
            nat_gateways=props.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateEgress",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=22,
                ),
                ec2.SubnetConfiguration(
                    name="PrivateIsolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
            flow_logs={
                "Cloudwatch": ec2.FlowLogOptions(
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(),
                    traffic_type=ec2.FlowLogTrafficType.ALL,
                    max_aggregation_interval=ec2.FlowLogMaxAggregationInterval.ONE_MINUTE,
                ),
            },
            restrict_default_security_group=True,
        )

        self.public_subnets = list(self.vpc.public_subnets)
        self.private_egress_subnets = list(self.vpc.private_subnets)
        self.private_isolated_subnets = list(self.vpc.isolated_subnets)

        account_scope_statement = self._account_scope_endpoint_statement()

        self.interface_endpoints = {}
        for short_name, aws_service_attr in _INTERFACE_ENDPOINTS:
            service = getattr(ec2.InterfaceVpcEndpointAwsService, aws_service_attr)
            endpoint = ec2.InterfaceVpcEndpoint(
                self,
                f"Endpoint{short_name}",
                vpc=self.vpc,
                service=service,
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
                private_dns_enabled=True,
            )
            endpoint.add_to_policy(account_scope_statement)
            self.interface_endpoints[short_name] = endpoint

        # S3 + DynamoDB gateway endpoints (free; attached to route tables).
        self.s3_gateway_endpoint = ec2.GatewayVpcEndpoint(
            self,
            "EndpointS3",
            vpc=self.vpc,
            service=ec2.GatewayVpcEndpointAwsService.S3,
        )
        self.s3_gateway_endpoint.add_to_policy(account_scope_statement)
        # ECR stores image layers in an AWS-owned S3 bucket
        # (prod-<region>-starport-layer-bucket), so the same-account scope
        # above (aws:ResourceAccount == this account) blocks ECS image pulls
        # from private subnets -> CannotPullContainerError and tasks never
        # start. Allow read-only GetObject on just that bucket; endpoint
        # policy Allows are OR'd, so this is the only cross-account exception
        # and every other bucket stays same-account-only.
        self.s3_gateway_endpoint.add_to_policy(
            iam.PolicyStatement(
                sid="AllowEcrImageLayerPull",
                effect=iam.Effect.ALLOW,
                principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
                actions=["s3:GetObject"],
                resources=[
                    f"arn:aws:s3:::prod-{cdk.Aws.REGION}-starport-layer-bucket/*"
                ],
            )
        )

        self.dynamodb_gateway_endpoint = ec2.GatewayVpcEndpoint(
            self,
            "EndpointDynamoDb",
            vpc=self.vpc,
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
        )
        self.dynamodb_gateway_endpoint.add_to_policy(account_scope_statement)

        # CDK auto-creates an IAM role + a CloudWatch log group for the VPC
        # Flow Logs publisher. We can't easily inject a KMS CMK or
        # managed-policy version through the high-level Vpc construct's
        # flow_logs= prop.
        NagSuppressions.add_resource_suppressions(
            self.vpc,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "VPC Flow Logs IAM role is created and managed by CDK as part of "
                        "the high-level Vpc construct's flow_logs= prop. We don't author "
                        "the policy and switching to a managed policy would require "
                        "dropping down to L1 constructs."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "VPC Flow Logs IAM role uses logs:CreateLogStream on the "
                        "destination log group with wildcards on log-stream name; "
                        "log-stream names are not pre-computable so wildcards are "
                        "necessary here."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-CloudWatchLogGroupEncrypted",
                    "reason": (
                        "TODO: tie the Flow Logs log group to `cert-ra-logs-cmk` once "
                        "DataStack lands and the CMK ARN is available. Tracked under L2 "
                        "in the security hardening backlog. NetworkStack is deployed "
                        "before DataStack at initial-setup; no clean way to forward-"
                        "reference the CMK here."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-VPCSubnetAutoAssignPublicIpDisabled",
                    "reason": (
                        "Public subnets host the ALB and NAT gateways — they must auto-"
                        "assign public IPs by definition. The private-egress and "
                        "private-isolated subnets (where ECS tasks and RDS live) do not "
                        "auto-assign public IPs."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-VPCNoUnrestrictedRouteToIGW",
                    "reason": (
                        "Public subnets have a default route to the Internet Gateway by "
                        "definition — that's what makes them public. The route is on "
                        "the public-subnet route tables only; private-egress subnets "
                        "route through NAT, and private-isolated subnets have no "
                        "internet route."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @staticmethod
    def _account_scope_endpoint_statement() -> iam.PolicyStatement:
        """H2-A: pin all endpoint-reachable resources to this AWS account.

        Without this, an interface endpoint is just a private route to the
        AWS service — code inside the VPC could still hit cross-account
        resources via the endpoint. The statement fails the request if the
        target resource lives outside our account.
        """
        account = cdk.Aws.ACCOUNT_ID
        return iam.PolicyStatement(
            sid="AllowSameAccountOnly",
            effect=iam.Effect.ALLOW,
            principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
            actions=["*"],
            resources=["*"],
            conditions={
                "StringEquals": {"aws:ResourceAccount": account},
            },
        )
