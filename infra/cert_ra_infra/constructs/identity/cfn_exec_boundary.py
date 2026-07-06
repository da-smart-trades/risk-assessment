# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

from aws_cdk import aws_iam as iam
from cdk_nag import NagSuppressions
from constructs import Construct


@dataclass(frozen=True, slots=True)
class CfnExecBoundaryProps:
    """Props for CfnExecBoundary. See § CDK bootstrap roles also carry a
    boundary (M1) in the design spec."""

    env: str
    """Deployment env (`staging` or `prod`). Suffix on the managed-policy
    name so both env IdentityStacks can own their own boundary without
    conflicting on the account-global IAM name."""

    @property
    def policy_name(self) -> str:
        return f"cert-ra-cfn-exec-boundary-{self.env}"


class CfnExecBoundary(Construct):
    """The custom permissions boundary attached to the CDK bootstrap
    cfn-exec-role via `cdk bootstrap --custom-permissions-boundary`.

    Looser than the human-session boundary (H3): allows
    iam:UpdateAssumeRolePolicy, route53:ChangeResourceRecordSets, etc. that
    CFN legitimately needs, but still cuts the persistence + ransom +
    DNS-takeover paths. See § CDK bootstrap roles also carry a boundary
    (M1) for the full reasoning.
    """

    policy: iam.ManagedPolicy

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: CfnExecBoundaryProps,
    ) -> None:
        super().__init__(scope, construct_id)

        statements = [
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["*"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="DenyOrgAndIdpChanges",
                effect=iam.Effect.DENY,
                actions=[
                    "organizations:*",
                    "account:*",
                    "sso:*",
                    "sso-admin:*",
                    "identitystore:*",
                    "iam:CreateOpenIDConnectProvider",
                    "iam:DeleteOpenIDConnectProvider",
                    "iam:UpdateOpenIDConnectProviderThumbprint",
                    "iam:CreateSAMLProvider",
                    "iam:DeleteSAMLProvider",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="DenyIamUserCreation",
                effect=iam.Effect.DENY,
                actions=[
                    "iam:CreateUser",
                    "iam:CreateAccessKey",
                    "iam:CreateLoginProfile",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="DenyAuditTrailDestruction",
                effect=iam.Effect.DENY,
                actions=[
                    "cloudtrail:DeleteTrail",
                    "cloudtrail:StopLogging",
                    "cloudtrail:PutEventSelectors",
                    "cloudtrail:UpdateTrail",
                    "logs:DeleteLogGroup",
                ],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="DenyKmsRansom",
                effect=iam.Effect.DENY,
                actions=["kms:ScheduleKeyDeletion", "kms:DisableKey"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                sid="DenyEcrCrossAccount",
                effect=iam.Effect.DENY,
                actions=["ecr:SetRepositoryPolicy"],
                resources=["*"],
                conditions={
                    "StringNotEquals": {
                        "aws:ResourceAccount": "${aws:PrincipalAccount}",
                    },
                },
            ),
        ]

        self.policy = iam.ManagedPolicy(
            self,
            "Policy",
            managed_policy_name=props.policy_name,
            description=(
                "Permissions boundary for CDK bootstrap cfn-exec-role. "
                "Looser than the human boundary; see design spec § CDK "
                "bootstrap roles also carry a boundary (M1)."
            ),
            document=iam.PolicyDocument(statements=statements),
        )

        # A permissions boundary deliberately has an `Allow *:*` base with
        # purpose-specific Denies layered on top — that's the architectural
        # shape. cdk-nag's NIST/IAM rules treat this as admin escalation;
        # the rationale is that the boundary is never an identity-attached
        # policy, only a boundary.
        NagSuppressions.add_resource_suppressions(
            self.policy,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": "Permissions boundary base allow; deny statements provide the actual scope.",
                },
                {
                    "id": "NIST.800.53.R5-IAMPolicyNoStatementsWithAdminAccess",
                    "reason": "Permissions boundary, not an identity policy. Allow * is bounded by deny statements + by the role's identity policy at deploy time.",
                },
                {
                    "id": "NIST.800.53.R5-IAMPolicyNoStatementsWithFullAccess",
                    "reason": "Same as above — boundary semantics, not identity policy.",
                },
            ],
            apply_to_children=True,
        )
