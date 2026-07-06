# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2

from cert_ra_infra.constructs.network.security_groups import (
    CertRaSecurityGroups,
    CertRaSecurityGroupsProps,
)


def _synth() -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2)
    CertRaSecurityGroups(stack, "Sgs", props=CertRaSecurityGroupsProps(vpc=vpc))
    return assertions.Template.from_stack(stack)


def test_all_seven_per_role_sgs_are_created() -> None:
    template = _synth()
    expected_names = {
        "cert-ra-alb-sg",
        "cert-ra-app-sg",
        "cert-ra-worker-sg",
        "cert-ra-temporal-fe-sg",
        "cert-ra-maint-sg",
        "cert-ra-migrate-sg",
        "cert-ra-rds-sg",
    }
    sgs = template.find_resources("AWS::EC2::SecurityGroup")
    found_names = {sg["Properties"].get("GroupName") for sg in sgs.values()}
    assert expected_names.issubset(found_names), (
        f"Missing SGs: {expected_names - found_names}"
    )


def test_alb_sg_allows_https_from_anywhere() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::EC2::SecurityGroup",
        {
            "GroupName": "cert-ra-alb-sg",
            "SecurityGroupIngress": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {"IpProtocol": "tcp", "FromPort": 443, "CidrIp": "0.0.0.0/0"}
                    ),
                ]
            ),
        },
    )


def test_maint_sg_has_no_zero_zero_zero_zero_egress() -> None:
    """H2-A: maint container must NOT have 0.0.0.0/0 egress (no NAT route).

    CDK creates an SG with `allow_all_outbound=False` -> no inline egress
    rule allowing 0.0.0.0/0. Egress rules are added explicitly per
    destination SG.
    """
    template = _synth()
    sgs = template.find_resources(
        "AWS::EC2::SecurityGroup",
        {"Properties": {"GroupName": "cert-ra-maint-sg"}},
    )
    (maint,) = sgs.values()
    egress = maint["Properties"].get("SecurityGroupEgress", [])
    for rule in egress:
        # CDK uses a 255.255.255.255/32 + IpProtocol=icmp dummy rule when
        # allow_all_outbound=False. Real 0.0.0.0/0 + tcp rules indicate
        # open egress, which would violate H2-A.
        cidr = rule.get("CidrIp")
        proto = rule.get("IpProtocol")
        if cidr == "0.0.0.0/0" and proto in {"tcp", "-1"}:
            raise AssertionError(f"Maint SG has open egress: {rule!r} — violates H2-A")


def test_rds_sg_receives_ingress_from_app_worker_maint_migrate() -> None:
    """The RDS SG should allow 5432 from each of the four data-touching roles."""
    template = _synth()
    # The ingress rules are emitted as separate AWS::EC2::SecurityGroupIngress
    # resources (since they reference the source SG).
    ingress_rules = template.find_resources("AWS::EC2::SecurityGroupIngress")
    rds_ingress_descs = {
        r["Properties"].get("Description")
        for r in ingress_rules.values()
        if r["Properties"].get("FromPort") == 5432
        and r["Properties"].get("ToPort") == 5432
    }
    expected = {
        "App to RDS",
        "Workers to RDS",
        "Maintenance to RDS",
        "Migration runner to RDS",
    }
    assert expected.issubset(rds_ingress_descs), (
        f"Missing RDS ingress: {expected - rds_ingress_descs}"
    )


def test_temporal_fe_receives_ingress_from_app_worker_maint() -> None:
    template = _synth()
    ingress_rules = template.find_resources("AWS::EC2::SecurityGroupIngress")
    fe_ingress_descs = {
        r["Properties"].get("Description")
        for r in ingress_rules.values()
        if r["Properties"].get("FromPort") == 7233
        and r["Properties"].get("ToPort") == 7233
    }
    expected = {
        "App to Temporal frontend",
        "Workers to Temporal frontend",
        "Maintenance to Temporal frontend",
    }
    assert expected.issubset(fe_ingress_descs), (
        f"Missing Temporal FE ingress: {expected - fe_ingress_descs}"
    )


def test_maint_egress_is_scoped_to_rds_temporal_and_s3() -> None:
    """The maintenance container should egress to RDS:5432, the Temporal
    frontend tasks directly (SG-to-SG, for direct gRPC), the Temporal
    frontend internal NLB (VPC CIDR, since the NLB has no SG), and the
    S3 gateway prefix list:443 (for ECR image layer pulls — the
    EcrApi/EcrDkr interface endpoints serve manifests but layer data
    downloads from S3). CDK splits the egress between standalone
    `AWS::EC2::SecurityGroupEgress` resources (for SG-to-SG / prefix
    list destinations) and an inlined `SecurityGroupEgress` array on
    the SG itself (for CIDR-based destinations), so we collect from
    both."""
    template = _synth()
    maint_descs: set[str] = set()
    egress_rules = template.find_resources("AWS::EC2::SecurityGroupEgress")
    for r in egress_rules.values():
        desc = r["Properties"].get("Description", "")
        if "Maint to" in str(desc):
            maint_descs.add(desc)
    sgs = template.find_resources("AWS::EC2::SecurityGroup")
    for r in sgs.values():
        if "maint" not in str(r["Properties"].get("GroupDescription", "")).lower():
            continue
        for eg in r["Properties"].get("SecurityGroupEgress", []):
            desc = eg.get("Description", "")
            if "Maint to" in str(desc):
                maint_descs.add(desc)
    assert maint_descs == {
        "Maint to RDS",
        "Maint to Temporal frontend (direct task)",
        "Maint to Temporal frontend (via internal NLB)",
        "Maint to S3 (ECR image layer pulls via gateway endpoint)",
    }, f"Maint egress is {maint_descs}; expected RDS + Temporal direct + Temporal NLB + S3"


def test_app_sg_receives_ingress_from_alb() -> None:
    template = _synth()
    ingress_rules = template.find_resources("AWS::EC2::SecurityGroupIngress")
    app_ingress = [
        r
        for r in ingress_rules.values()
        if r["Properties"].get("Description") == "ALB to App container"
    ]
    assert len(app_ingress) == 1
    rule = app_ingress[0]
    assert rule["Properties"]["FromPort"] == 8000
