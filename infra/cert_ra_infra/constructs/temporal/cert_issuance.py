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

_LAMBDA_ASSET_DIR = Path(__file__).parent / "_lambda_assets" / "initial_issuance"
DEFAULT_VALIDITY_DAYS = 397  # ~13 months (A6)


@dataclass(frozen=True, slots=True)
class TemporalServiceCertConfig:
    """One end-entity cert the InitialCertIssuance Lambda will issue."""

    name: str
    """Service identifier, e.g. `temporal-frontend`, `worker-metrics`."""

    secret_arn: str
    """SecretsStack-owned `SeededSecret.secret_arn` to populate."""

    secret_name: str
    """The `secret_name` (path form, e.g. `/cert-ra/staging/temporal/mtls/<name>`)
    — what the Lambda passes to `secretsmanager:PutSecretValue --SecretId`."""

    common_name: str
    """Cert subject CN, e.g. `temporal-frontend.cert-ra.local`."""

    validity_days: int = DEFAULT_VALIDITY_DAYS


@dataclass(frozen=True, slots=True)
class InitialCertIssuanceProps:
    """Props for InitialCertIssuance. See § Initial cert population —
    synchronous Custom Resource (B1) in the design spec."""

    subordinate_ca_arn: str
    """ARN of `TemporalMtlsPki.subordinate_ca`."""

    services: list[TemporalServiceCertConfig]
    """The five end-entity certs to issue: temporal-frontend, three
    workers, and maint."""

    secrets_cmk_arn: str
    """ARN of `cert-ra-secrets-cmk` from SecretsStack. Needed so the
    Lambda can `kms:Encrypt`/`GenerateDataKey` when writing to the
    KMS-encrypted secrets."""


class InitialCertIssuance(Construct):
    """CDK Custom Resource that issues the initial Temporal mTLS certs.

    On stack `Create`:
        - For each service, the Lambda generates a private key + CSR,
          calls `acm-pca:IssueCertificate` against the subordinate CA,
          polls until issuance completes, and writes `(cert, chain, key)`
          to the corresponding Secrets Manager entry.
        - Returns success only when ALL services are populated, so the
          phase-2 TemporalStack redeploy (mTLS-on) can safely assume cert
          availability.

    On stack `Update`: mint certs for any service whose secret is still
    on its SeededSecret placeholder. Existing certs are left alone — the
    daily renewal Lambda handles rotation. This lets an operator add a
    new entry to `_MTLS_SERVICE_NAMES` (e.g. when wiring a new client
    workload into the cluster) and have the cert appear on the next
    `cdk deploy` without a manual `aws lambda invoke` against the
    renewal handler. Re-running deploys with no service-list changes is
    a true no-op because every secret is already populated.

    On stack `Delete`: no-op (secrets are `RemovalPolicy.RETAIN`).

    **Cross-stack note:** The secrets live in `SecretsStack`. Using
    `secret.grant_write(fn)` would mutate each secret's resource policy
    in SecretsStack, creating a `SecretsStack → TemporalStack`
    dependency on top of the existing `TemporalStack → SecretsStack`
    dependency (TemporalStack references the secret ARNs). To avoid the
    cycle, we accept ARNs as strings and attach identity-side IAM
    statements only. The default Secrets Manager resource policy
    accepts in-account principals; the M3 MFA-on-writes policy on
    OAuth/session-secret doesn't apply because Temporal mTLS shells
    aren't M3-gated.
    """

    handler_fn: lambda_.Function
    custom_resource: cdk.CustomResource

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: InitialCertIssuanceProps,
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
                "Initial Temporal mTLS cert issuance (B1 path 1). "
                "Runs once on TemporalStack create."
            ),
        )

        # IAM: acm-pca operations against the subordinate CA only.
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
        # (identity-side only; see class docstring for cycle-avoidance
        # reasoning). GetSecretValue is needed for the Update path: the
        # handler reads each secret to check whether it's still on its
        # placeholder, and only mints a fresh cert if so. The Create
        # path doesn't need the read but the policy is unified for
        # simplicity.
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="ReadAndWriteTemporalMtlsSecrets",
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                ],
                resources=[svc.secret_arn for svc in props.services],
            )
        )

        # IAM: KMS encrypt with the secrets CMK so Secrets Manager can
        # store the new values.
        self.handler_fn.add_to_role_policy(
            iam.PolicyStatement(
                sid="EncryptViaSecretsCmk",
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

        # Provider framework handles the CFN polling + status reporting.
        provider = custom_resources.Provider(
            self,
            "Provider",
            on_event_handler=self.handler_fn,  # pyright: ignore[reportArgumentType]
        )

        self.custom_resource = cdk.CustomResource(
            self,
            "Resource",
            service_token=provider.service_token,
            resource_type="Custom::TemporalMtlsInitialCertIssuance",
            properties={
                "SubordinateCaArn": props.subordinate_ca_arn,
                "Services": [
                    {
                        "Name": svc.name,
                        "SecretName": svc.secret_name,
                        "CommonName": svc.common_name,
                        "ValidityDays": svc.validity_days,
                    }
                    for svc in props.services
                ],
            },
        )

        # CDK's Provider framework + Lambda construct create framework-level
        # IAM (CW Logs role, Provider framework's onEvent role) with inline
        # policies and wildcards we don't author. Plus the
        # one-shot-Custom-Resource Lambda doesn't need DLQ/VPC/concurrency
        # — CFN serialises invocations and surfaces failures via stack rollback.
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
                        "to the framework Lambdas — that's the AWS-recommended "
                        "pattern for the Provider scaffolding."
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
                    "reason": (
                        "One-shot Custom Resource invocation. CFN serialises "
                        "Create/Update events per resource, so a concurrency "
                        "limit adds no protection."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaDLQ",
                    "reason": (
                        "Custom Resource failures are surfaced via CFN stack "
                        "events + automatic rollback; a separate DLQ would "
                        "duplicate the signal."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-LambdaInsideVPC",
                    "reason": (
                        "The handler only calls ACM PCA, Secrets Manager, and "
                        "KMS via the public AWS endpoints. Putting it in a VPC "
                        "would require VPC endpoints for each service without "
                        "measurable benefit since the Lambda's IAM scope is "
                        "already tight (per-CA, per-secret, per-CMK)."
                    ),
                },
            ],
            apply_to_children=True,
        )
