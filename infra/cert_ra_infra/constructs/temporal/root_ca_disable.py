# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import custom_resources
from cdk_nag import NagSuppressions
from constructs import Construct

_LAMBDA_ASSET_DIR = Path(__file__).parent / "_lambda_assets" / "root_ca_disable"


@dataclass(frozen=True, slots=True)
class RootCaDisableProps:
    """Props for RootCaDisable. See § PCA structure (B2 resolved) in
    the design spec."""

    root_ca_arn: str
    """ARN of `TemporalMtlsPki.root_ca`."""


class RootCaDisable(Construct):
    """CDK Custom Resource that disables the Temporal mTLS root CA on
    stack create — completing B2.

    Once the subordinate CA is signed by the root and activated, the
    root CA's job is done. Disabling it limits the blast radius if
    Installer is later compromised: the attacker would need to re-enable
    the root (CloudTrail-visible, MFA-gated via the boundary) before
    they could issue a rogue subordinate.

    Lambda dependencies: only boto3 (in the Lambda Python runtime).
    No `cryptography` or other external packages, so the bundling step
    just copies the handler file with no pip install layer.
    """

    handler_fn: lambda_.Function
    custom_resource: cdk.CustomResource

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: RootCaDisableProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.handler_fn = lambda_.Function(
            self,
            "Handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(_LAMBDA_ASSET_DIR),
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        # No external deps; just copy handler.py.
                        "cp -r . /asset-output && rm -rf /asset-output/__pycache__",
                    ],
                ),
            ),
            timeout=cdk.Duration.minutes(5),
            memory_size=128,
            description=(
                "Disables the Temporal mTLS root CA after subordinate "
                "issuance (B2). Runs once on stack create."
            ),
        )

        # IAM: only UpdateCertificateAuthority on the root CA ARN.
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="DisableRootCa",
                effect=iam.Effect.ALLOW,
                actions=["acm-pca:UpdateCertificateAuthority"],
                resources=[props.root_ca_arn],
            )
        )

        provider = custom_resources.Provider(
            self,
            "Provider",
            on_event_handler=self.handler_fn,  # pyright: ignore[reportArgumentType]
        )

        self.custom_resource = cdk.CustomResource(
            self,
            "Resource",
            service_token=provider.service_token,
            resource_type="Custom::TemporalRootCaDisable",
            properties={"RootCaArn": props.root_ca_arn},
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "CDK Lambda + Provider framework auto-generate roles "
                        "with inline policies (CW Logs writer, event loop "
                        "handler). We don't author them."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Lambda's CW Logs writer policy uses wildcards on "
                        "log-stream name; stream names aren't pre-computable."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": (
                        "CDK Provider framework attaches AWSLambdaBasicExecutionRole "
                        "to the framework Lambdas — AWS-recommended pattern."
                    ),
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Provider framework's internal Lambdas may use an older "
                        "runtime than our handler; we pin our handler to Python "
                        "3.12 but don't control the framework's internals."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaConcurrency",
                    "reason": ("One-shot Custom Resource; CFN serialises invocations."),
                },
                {
                    "id": "NIST.800.53.R5-LambdaDLQ",
                    "reason": (
                        "Custom Resource failures surface via CFN stack events "
                        "+ automatic rollback."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaInsideVPC",
                    "reason": (
                        "Handler only calls acm-pca via the public AWS endpoint; "
                        "IAM is tight (single CA ARN + single action)."
                    ),
                },
            ],
            apply_to_children=True,
        )
