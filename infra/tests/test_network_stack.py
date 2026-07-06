# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.network import NetworkStack


def _synth_stack(env_name: str = "staging") -> assertions.Template:
    app = cdk.App()
    cfg = load_env(env_name)
    env = cdk.Environment(account="111111111111", region=cfg.region)
    stack = NetworkStack(
        app,
        f"CertRa-NetworkStack-{cfg.env}",
        env=env,
        env_config=cfg,
    )
    return assertions.Template.from_stack(stack)


def test_staging_uses_two_azs_and_one_nat() -> None:
    template = _synth_stack("staging")
    template.resource_count_is("AWS::EC2::NatGateway", 1)
    # 2 AZs x 3 subnet tiers = 6
    template.resource_count_is("AWS::EC2::Subnet", 6)


def test_prod_uses_three_azs_and_three_nats() -> None:
    template = _synth_stack("prod")
    template.resource_count_is("AWS::EC2::NatGateway", 3)
    template.resource_count_is("AWS::EC2::Subnet", 9)


def test_staging_uses_correct_vpc_cidr() -> None:
    template = _synth_stack("staging")
    template.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.0.0.0/16"})


def test_prod_uses_distinct_vpc_cidr_for_future_peering() -> None:
    template = _synth_stack("prod")
    template.has_resource_properties("AWS::EC2::VPC", {"CidrBlock": "10.1.0.0/16"})


def test_stack_creates_per_role_security_groups() -> None:
    template = _synth_stack("staging")
    sgs = template.find_resources("AWS::EC2::SecurityGroup")
    names = {sg["Properties"].get("GroupName") for sg in sgs.values()}
    for required in (
        "cert-ra-alb-sg",
        "cert-ra-app-sg",
        "cert-ra-worker-sg",
        "cert-ra-temporal-fe-sg",
        "cert-ra-maint-sg",
        "cert-ra-migrate-sg",
        "cert-ra-rds-sg",
    ):
        assert required in names, f"Missing SG: {required}"


def test_stack_creates_internet_facing_alb_with_env_name() -> None:
    template = _synth_stack("staging")
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {"Scheme": "internet-facing", "Name": "cert-ra-staging"},
    )


def test_stack_exports_cfn_outputs() -> None:
    template = _synth_stack("staging")
    outputs = template.find_outputs("*")
    required = {
        "VpcId",
        "PrivateEgressSubnetIds",
        "PrivateIsolatedSubnetIds",
        "AlbArn",
        "AlbDnsName",
    }
    assert required.issubset(set(outputs.keys()))


def test_stack_flow_logs_present() -> None:
    """H2-C requires VPC flow logs for the maint REJECT alarm."""
    template = _synth_stack("staging")
    template.resource_count_is("AWS::EC2::FlowLog", 1)
