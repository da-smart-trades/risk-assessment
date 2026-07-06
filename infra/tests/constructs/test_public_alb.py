# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2

from cert_ra_infra.constructs.network.public_alb import PublicAlb, PublicAlbProps


def _synth(*, alb_name: str = "cert-ra-alb") -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2)
    sg = ec2.SecurityGroup(stack, "Sg", vpc=vpc)
    PublicAlb(
        stack,
        "Alb",
        props=PublicAlbProps(vpc=vpc, alb_security_group=sg, alb_name=alb_name),
    )
    return assertions.Template.from_stack(stack)


def test_alb_is_internet_facing() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {"Scheme": "internet-facing"},
    )


def test_alb_name_is_set() -> None:
    template = _synth(alb_name="cert-ra-prod")
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {"Name": "cert-ra-prod"},
    )


def test_alb_drops_invalid_header_fields() -> None:
    """Best-practice: prevent host header injection / request smuggling."""
    template = _synth()
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {
            "LoadBalancerAttributes": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "Key": "routing.http.drop_invalid_header_fields.enabled",
                            "Value": "true",
                        }
                    ),
                ]
            ),
        },
    )


def test_no_listeners_are_created_at_this_layer() -> None:
    """LitestarService adds listeners later — NetworkStack provides only the shell."""
    template = _synth()
    template.resource_count_is("AWS::ElasticLoadBalancingV2::Listener", 0)
    template.resource_count_is("AWS::ElasticLoadBalancingV2::TargetGroup", 0)


def test_alb_placed_in_public_subnets() -> None:
    template = _synth()
    albs = template.find_resources("AWS::ElasticLoadBalancingV2::LoadBalancer")
    (alb,) = albs.values()
    # ALB references subnets via SubnetMappings or Subnets
    subnets = alb["Properties"].get("Subnets", [])
    assert len(subnets) >= 2, (
        "ALB must span at least two public subnets for AZ redundancy"
    )
