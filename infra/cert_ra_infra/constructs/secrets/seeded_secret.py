# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

PLACEHOLDER_VALUE = "__SEED_ME__"
"""Sentinel placeholder. `seed-secrets.py` refuses to overwrite a secret
unless its current value contains this marker (or `--force` is passed)."""


@dataclass(frozen=True, slots=True)
class SeededSecretProps:
    """Props for SeededSecret. See § Secrets rotation and § MFA-gated writes
    for high-risk secrets (M3) in the design spec."""

    secret_name: str
    """Full path under which the secret lives (e.g. `/cert-ra/staging/oauth/providers`)."""

    description: str

    encryption_key: kms.IKey
    """The `cert-ra-secrets-cmk` from SecretsStack."""

    placeholder_value: str = PLACEHOLDER_VALUE
    """The initial value written into the secret shell. `seed-secrets.py`
    checks for the `__SEED_ME__` substring before overwriting."""

    require_mfa_for_writes: bool = False
    """M3: when True, attach a resource policy denying
    `secretsmanager:PutSecretValue`/`UpdateSecret`/`DeleteSecret`/etc.
    unless `aws:MultiFactorAuthPresent` is true. Uses `BoolIfExists` so
    service identities (e.g. SAR rotation Lambdas) are unaffected."""

    installer_role_arn_pattern: str | None = None
    """When set alongside `require_mfa_for_writes`, the Installer role is
    exempted from the MFA deny via a `StringNotLike` condition on
    `aws:PrincipalArn`. Required when the IdP (e.g. Google Workspace) does
    not propagate `aws:MultiFactorAuthPresent=true` through SSO sessions,
    which would otherwise block the initial secret-seeding step."""


class SeededSecret(Construct):
    """A Secrets Manager secret pre-populated with a placeholder value.

    Workflow:
    1. SecretsStack creates the shell with `__SEED_ME__` as the value.
    2. `scripts/seed-secrets.py` (run interactively under an SSO session)
       prompts the operator for the real value and calls
       `PutSecretValue`. The script detects the placeholder and refuses
       to overwrite real values without `--force`.
    3. Application services read the secret via the ECS execution role.

    For Temporal mTLS shells (M5/B1), the real value is written by a
    Lambda backing a CDK Custom Resource in TemporalStack, not by
    `seed-secrets.py`. The placeholder still acts as a "not yet
    populated" marker for monitoring.
    """

    secret: secretsmanager.Secret

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: SeededSecretProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.secret = secretsmanager.Secret(
            self,
            "Secret",
            secret_name=props.secret_name,
            description=props.description,
            encryption_key=props.encryption_key,
            secret_string_value=cdk.SecretValue.unsafe_plain_text(
                props.placeholder_value
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        if props.require_mfa_for_writes:
            conditions: dict[str, object] = {
                "BoolIfExists": {
                    "aws:MultiFactorAuthPresent": "false",
                },
            }
            if props.installer_role_arn_pattern:
                # Google Workspace (and some other external IdPs) does not
                # propagate aws:MultiFactorAuthPresent=true through SSO
                # sessions, so the Installer would be blocked by the deny
                # even after a valid MFA sign-in. Exempt the Installer role
                # via StringNotLike so initial seeding (and future manual
                # rotation) works. The Installer is SSO-gated and every
                # call is logged in CloudTrail, preserving the audit trail
                # that the MFA condition was meant to provide.
                conditions["StringNotLike"] = {
                    "aws:PrincipalArn": props.installer_role_arn_pattern,
                }
            self.secret.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="DenyMutationsWithoutMfa",
                    effect=iam.Effect.DENY,
                    principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
                    actions=[
                        "secretsmanager:PutSecretValue",
                        "secretsmanager:UpdateSecret",
                        "secretsmanager:DeleteSecret",
                        "secretsmanager:RestoreSecret",
                        "secretsmanager:CancelRotateSecret",
                        "secretsmanager:RotateSecret",
                    ],
                    resources=["*"],
                    conditions=conditions,
                )
            )

        # Per Q5, only the RDS master credential auto-rotates. Every
        # SeededSecret is on a manual rotation cadence documented in
        # `docs/secrets-rotation.md`; cdk-nag's blanket "rotation
        # required" rules are suppressed here with that pointer.
        NagSuppressions.add_resource_suppressions(
            self.secret,
            [
                {
                    "id": "AwsSolutions-SMG4",
                    "reason": (
                        "Manual rotation per Q5 — see § Secrets rotation in the "
                        "design spec. Auto-rotation only applies to the RDS "
                        "master credential; everything else has a documented "
                        "manual cadence in docs/secrets-rotation.md."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-SecretsManagerRotationEnabled",
                    "reason": "Same as AwsSolutions-SMG4.",
                },
            ],
        )

    @property
    def secret_arn(self) -> str:
        return self.secret.secret_arn

    @property
    def secret_name(self) -> str:
        return self.secret.secret_name
