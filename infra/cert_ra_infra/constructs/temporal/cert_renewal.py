# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as events_targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from cdk_nag import NagSuppressions
from constructs import Construct

from cert_ra_infra.constructs.temporal.cert_issuance import (
    TemporalServiceCertConfig,
)

_LAMBDA_ASSET_DIR = Path(__file__).parent / "_lambda_assets" / "renewal_handler"

DEFAULT_RENEW_WHEN_DAYS_REMAINING = 159
"""~40% of 397-day validity — matches ACM PCA's standard 60% renewal point."""


@dataclass(frozen=True, slots=True)
class CertRenewalProps:
    """Props for CertRenewal. See § Cert rotation (B1 path 2) in the design
    spec."""

    subordinate_ca_arn: str
    """ARN of `TemporalMtlsPki.subordinate_ca` (same one InitialCertIssuance uses)."""

    services: list[TemporalServiceCertConfig]
    """Same five service configs the initial Custom Resource populated.
    The renewal handler walks the list daily, checking each cert's
    expiration and re-issuing as needed."""

    secrets_cmk_arn: str
    """`cert-ra-secrets-cmk` for writing the renewed certs."""

    renew_when_days_remaining: int = DEFAULT_RENEW_WHEN_DAYS_REMAINING

    schedule: events.Schedule | None = None
    """Defaults to daily at 02:00 UTC if omitted."""


class CertRenewal(Construct):
    """Scheduled Lambda that renews Temporal mTLS end-entity certs.

    Runs daily; for each service, reads the current cert from Secrets
    Manager, parses the expiration date, and if less than
    `renew_when_days_remaining` days remain, re-issues a fresh
    (key, cert) pair from the subordinate CA. Otherwise skips.

    Workers pick up the renewed cert at next task restart. PutSecretValue
    rotates the prior version to AWSPREVIOUS so operators can roll back
    via `secretsmanager:UpdateSecretVersionStage` if a renewal goes bad.

    **Cross-stack note** (same as InitialCertIssuance): we accept secret
    ARNs as strings to avoid mutating SecretsStack's resource policies
    from TemporalStack and creating a cross-stack dependency cycle.
    """

    handler_fn: lambda_.Function
    schedule_rule: events.Rule

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: CertRenewalProps,
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
                        "pip install -r requirements.txt -t /asset-output && "
                        "cp -r . /asset-output && "
                        "rm -rf /asset-output/__pycache__",
                    ],
                ),
            ),
            timeout=cdk.Duration.minutes(15),
            memory_size=256,
            description=(
                "Daily renewal of Temporal mTLS end-entity certs (B1 path 2). "
                "Checks each cert's expiration; re-issues if within "
                f"{props.renew_when_days_remaining} days of expiry."
            ),
        )

        # IAM: PCA operations against the subordinate.
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="IssueCertsAgainstSubordinate",
                effect=iam.Effect.ALLOW,
                actions=[
                    "acm-pca:IssueCertificate",
                    "acm-pca:GetCertificate",
                ],
                resources=[props.subordinate_ca_arn],
            )
        )

        # IAM: GetSecretValue + PutSecretValue on each target secret
        # (GetSecretValue lets the handler parse the existing cert's
        # expiration; PutSecretValue overwrites with the renewed payload).
        secret_arns = [svc.secret_arn for svc in props.services]
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadAndWriteTemporalMtlsSecrets",
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                ],
                resources=secret_arns,
            )
        )

        # IAM: KMS for the secrets CMK — both Decrypt (to read existing
        # cert payloads) and Encrypt/GenerateDataKey (to write new ones).
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="UseSecretsCmk",
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Encrypt",
                    "kms:Decrypt",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources=[props.secrets_cmk_arn],
            )
        )

        schedule = props.schedule or events.Schedule.cron(
            minute="0",
            hour="2",
            day="*",
            month="*",
            year="*",
        )

        self.schedule_rule = events.Rule(
            self,
            "DailySchedule",
            schedule=schedule,
            description=(
                "Trigger Temporal mTLS cert renewal check (B1 path 2). "
                "Daily at 02:00 UTC by default."
            ),
        )

        event_payload = {
            "SubordinateCaArn": props.subordinate_ca_arn,
            "RenewWhenDaysRemaining": props.renew_when_days_remaining,
            "Services": [
                {
                    "Name": svc.name,
                    "SecretName": svc.secret_name,
                    "CommonName": svc.common_name,
                    "ValidityDays": svc.validity_days,
                }
                for svc in props.services
            ],
        }

        self.schedule_rule.add_target(
            events_targets.LambdaFunction(
                self.handler_fn,  # pyright: ignore[reportArgumentType]
                event=events.RuleTargetInput.from_object(event_payload),
            )
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "CDK Lambda construct auto-generates a role with an "
                        "inline CW Logs policy. We don't author it."
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
                        "Lambda execution role uses AWSLambdaBasicExecutionRole — "
                        "AWS-recommended for CW Logs writes."
                    ),
                },
                {
                    "id": "AwsSolutions-L1",
                    "reason": (
                        "Python 3.12 is pinned per § Container image baselines "
                        "(B4) — bumping the runtime is a documented change, not "
                        "a per-deploy decision."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaConcurrency",
                    "reason": (
                        "Scheduled daily invocation; EventBridge serialises "
                        "deliveries per rule, so a concurrency limit adds no "
                        "protection."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaDLQ",
                    "reason": (
                        "Failed renewals are detected on next scheduled run "
                        "(retry semantics). A DLQ would add a second alerting "
                        "path without preventing the next-day retry. CloudWatch "
                        "alarms on the handler's error metric are the canonical "
                        "alerting path."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaInsideVPC",
                    "reason": (
                        "The handler only calls ACM PCA, Secrets Manager, and "
                        "KMS via the public AWS endpoints. VPC placement would "
                        "require endpoints for each service without measurable "
                        "benefit; IAM scope is already tight (per-CA, per-secret, "
                        "per-CMK)."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def handler_function_arn(self) -> str:
        return self.handler_fn.function_arn

    @property
    def schedule_rule_arn(self) -> str:
        return self.schedule_rule.rule_arn
