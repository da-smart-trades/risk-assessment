# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.cfn_exec_boundary import (
    CfnExecBoundary,
    CfnExecBoundaryProps,
)


def _synth(props: CfnExecBoundaryProps | None = None) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    CfnExecBoundary(stack, "Boundary", props=props or CfnExecBoundaryProps(env="test"))
    return assertions.Template.from_stack(stack)


def test_policy_name_includes_env_suffix() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {"ManagedPolicyName": "cert-ra-cfn-exec-boundary-test"},
    )


def test_policy_name_varies_per_env() -> None:
    template = _synth(CfnExecBoundaryProps(env="prod"))
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {"ManagedPolicyName": "cert-ra-cfn-exec-boundary-prod"},
    )


def test_allows_star_star_as_first_statement() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::ManagedPolicy")
    (props,) = (p["Properties"] for p in policies.values())
    statements = props["PolicyDocument"]["Statement"]
    assert statements[0] == {
        "Effect": "Allow",
        "Action": "*",
        "Resource": "*",
    }


def test_denies_federated_idp_addition() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyOrgAndIdpChanges",
                                "Effect": "Deny",
                                "Action": assertions.Match.array_with(
                                    [
                                        "iam:CreateOpenIDConnectProvider",
                                        "iam:CreateSAMLProvider",
                                    ]
                                ),
                            }
                        ),
                    ]
                )
            }
        },
    )


def test_denies_audit_trail_destruction() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyAuditTrailDestruction",
                                "Effect": "Deny",
                                "Action": assertions.Match.array_with(
                                    ["cloudtrail:DeleteTrail", "logs:DeleteLogGroup"]
                                ),
                            }
                        ),
                    ]
                )
            }
        },
    )


def test_denies_kms_ransom() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyKmsRansom",
                                "Effect": "Deny",
                                "Action": ["kms:ScheduleKeyDeletion", "kms:DisableKey"],
                            }
                        ),
                    ]
                )
            }
        },
    )


def test_denies_cross_account_ecr_policy() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyEcrCrossAccount",
                                "Effect": "Deny",
                                "Action": "ecr:SetRepositoryPolicy",
                                "Condition": {
                                    "StringNotEquals": {
                                        "aws:ResourceAccount": "${aws:PrincipalAccount}",
                                    }
                                },
                            }
                        ),
                    ]
                )
            }
        },
    )
