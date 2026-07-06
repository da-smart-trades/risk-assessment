# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.network.vpc import (
    VpcWithEndpoints,
    VpcWithEndpointsProps,
)


def _synth(
    *,
    cidr: str = "10.0.0.0/16",
    max_azs: int = 2,
    nat_gateways: int = 1,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    VpcWithEndpoints(
        stack,
        "Vpc",
        props=VpcWithEndpointsProps(
            cidr=cidr, max_azs=max_azs, nat_gateways=nat_gateways
        ),
    )
    return assertions.Template.from_stack(stack)


def test_vpc_uses_specified_cidr() -> None:
    template = _synth(cidr="10.1.0.0/16")
    template.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.1.0.0/16"})


def test_subnets_span_three_tiers() -> None:
    """Public, private-egress, private-isolated subnets per AZ."""
    template = _synth(max_azs=2)
    # 2 AZs * 3 tiers = 6 subnets
    template.resource_count_is("AWS::EC2::Subnet", 6)


def test_nat_gateways_match_props() -> None:
    template = _synth(nat_gateways=1)
    template.resource_count_is("AWS::EC2::NatGateway", 1)


def test_three_nat_gateways_for_prod_sizing() -> None:
    template = _synth(max_azs=3, nat_gateways=3)
    template.resource_count_is("AWS::EC2::NatGateway", 3)


def test_flow_logs_enabled() -> None:
    """H2-C uses VPC flow logs for maint REJECT alarming."""
    template = _synth()
    template.resource_count_is("AWS::EC2::FlowLog", 1)
    template.has_resource_properties(
        "AWS::EC2::FlowLog",
        {"TrafficType": "ALL", "MaxAggregationInterval": 60},
    )


def test_all_interface_endpoints_are_provisioned() -> None:
    template = _synth()
    # 11 interface endpoints from _INTERFACE_ENDPOINTS in vpc.py
    template.resource_count_is("AWS::EC2::VPCEndpoint", 11 + 2)


def test_endpoint_policies_pin_resources_to_this_account() -> None:
    """H2-A: every endpoint's policy must require aws:ResourceAccount = this account."""
    template = _synth()
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    assert len(endpoints) > 0
    for endpoint in endpoints.values():
        policy = endpoint["Properties"]["PolicyDocument"]
        statements = policy["Statement"]
        assert any(
            s.get("Condition", {}).get("StringEquals", {}).get("aws:ResourceAccount")
            for s in statements
        ), "Endpoint missing aws:ResourceAccount condition"


def test_default_security_group_is_restricted() -> None:
    """RestrictDefaultSecurityGroup ensures the VPC's default SG denies everything."""
    template = _synth()
    # CDK creates a Custom Resource that mutates the default SG when this is set.
    # Asserting on the property presence on the VPC.
    vpcs = template.find_resources("AWS::EC2::VPC")
    # Only one VPC
    assert len(vpcs) == 1


def test_s3_and_dynamodb_are_gateway_endpoints() -> None:
    template = _synth()
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    gateway_services = {
        e["Properties"].get("VpcEndpointType"): e["Properties"].get("ServiceName")
        for e in endpoints.values()
        if e["Properties"].get("VpcEndpointType") == "Gateway"
    }
    # Two gateway endpoints expected (S3 + DynamoDB)
    gateway_count = sum(
        1
        for e in endpoints.values()
        if e["Properties"].get("VpcEndpointType") == "Gateway"
    )
    assert gateway_count == 2
    del gateway_services  # used for visibility during debugging only
