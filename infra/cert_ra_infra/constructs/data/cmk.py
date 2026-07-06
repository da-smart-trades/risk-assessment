# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct

KmsPurpose = Literal["encrypt", "sign-verify"]

_ENCRYPT_PRINCIPAL_ACTIONS = [
    "kms:Encrypt",
    "kms:Decrypt",
    "kms:ReEncrypt*",
    "kms:GenerateDataKey*",
    "kms:DescribeKey",
]

_SIGN_VERIFY_PRINCIPAL_ACTIONS = [
    "kms:Sign",
    "kms:Verify",
    "kms:GetPublicKey",
    "kms:DescribeKey",
]

_ADMIN_ACTIONS = [
    "kms:Create*",
    "kms:Update*",
    "kms:Tag*",
    "kms:Untag*",
    "kms:Enable*",
    "kms:Disable*",
    "kms:Describe*",
    "kms:Get*",
    "kms:List*",
    # `kms:Delete*` covers DeleteAlias (CFN calls this when a CMK alias
    # resource is deleted from a stack) and DeleteImportedKeyMaterial.
    # `kms:ScheduleKeyDeletion` itself is denied unconditionally by the
    # M2 cfn-exec boundary's DenyKmsRansom Sid, so widening Delete*
    # here doesn't open a path to silent key destruction.
    "kms:Delete*",
]

# CDK bootstrap creates a cfn-exec-role with a deterministic name
# pattern: `cdk-<qualifier>-cfn-exec-role-<account>-<region>`. The
# default qualifier is `hnb659fds`; custom qualifiers (rare) match
# the `*` in the middle. This role is what CloudFormation assumes
# when creating + updating resources, including calling
# `kms:EnableKeyRotation` on a CMK after CreateKey. Without granting
# it AdminOperations access via the key policy, the deploy fails with
# `Access denied for operation 'EnableKeyRotation'`.
_CDK_CFN_EXEC_ROLE_ARN_PATTERN = "arn:aws:iam::*:role/cdk-*-cfn-exec-role-*"


def _empty_str_list() -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class NarrowKmsCmkProps:
    """Props for NarrowKmsCmk. See § KMS key policies (M2) in the design spec."""

    key_id: str
    """Short identifier; combined with `env` to form `alias/cert-ra-<key_id>-<env>`."""

    env: str
    """Deployment env (`staging` or `prod`). Appended to the KMS alias so the
    same construct can be instantiated in both env stacks without colliding on
    the account-global alias namespace."""

    purpose: KmsPurpose
    """`encrypt` → SYMMETRIC_DEFAULT; `sign-verify` → ECC_NIST_P256."""

    service_principals: list[str] = field(default_factory=_empty_str_list)
    """AWS service principals allowed to use the key (e.g. `rds.amazonaws.com`)."""

    service_linked_roles: list[str] = field(default_factory=_empty_str_list)
    """ARNs of service-linked or task roles allowed to use the key."""

    admin_roles: list[str] = field(default_factory=_empty_str_list)
    """ARNs of roles allowed to administer the key lifecycle."""

    delegate_via_services: list[str] = field(default_factory=_empty_str_list)
    """AWS services that should be authorized to use the key on behalf of
    in-account IAM principals (e.g. `["s3"]`). Each entry expands into a
    key policy statement that allows any account-internal principal to call
    the encrypt actions on the key **only when** `kms:ViaService` matches
    `<service>.<region>.amazonaws.com`. Required for SSE-KMS workflows
    where the caller (not the service principal) makes the KMS call —
    notably S3 PutObject + GetObject. Authorization still requires the
    caller's identity policy to grant the KMS action; this just removes
    the resource-policy block."""


class NarrowKmsCmk(Construct):
    """KMS CMK with an explicit per-principal key policy.

    Replaces the CDK default policy (which delegates to root + IAM) with a
    narrow allowlist + structural denies. See § KMS key policies (M2).
    """

    key: kms.Key

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: NarrowKmsCmkProps,
    ) -> None:
        super().__init__(scope, construct_id)

        account_id = cdk.Stack.of(self).account

        # KMS principal ARNs not only require a concrete account ID but
        # are also validated against the AWS API for existence + concrete
        # role-name match. The SSO role pattern
        # `arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_CertRaInstaller_*`
        # gets rejected on BOTH counts: `*` in the account position is
        # malformed, and `*` in the role-name position can't resolve to a
        # specific role even after the SSO permission set is provisioned.
        #
        # For service-linked roles we still substitute the account
        # wildcard and use ArnPrincipal — SLR ARNs are typically
        # concrete (no path wildcards) so they resolve fine.
        #
        # For admin roles we switch to a different shape: principal is
        # the account root (always valid; KMS doesn't validate it), and
        # we restrict to the actual SSO role family via an ArnLike
        # condition on `aws:PrincipalArn`. IAM conditions accept
        # wildcards in any ARN component, so this preserves the
        # intended access scoping without tripping KMS validation.

        def _resolve_account_wildcard(arns: list[str]) -> list[str]:
            return [
                arn.replace(":*:", f":{account_id}:", 1)
                if arn.startswith("arn:aws:iam::*:")
                else arn
                for arn in arns
            ]

        service_linked_role_principals = _resolve_account_wildcard(
            list(props.service_linked_roles)
        )
        # ArnLike condition accepts wildcards in any ARN component, so
        # we pass the original `admin_roles` strings through unchanged.
        # The `:*:` account-position wildcard works here because the
        # condition matches against the runtime caller's PrincipalArn,
        # which always has a concrete account ID.
        #
        # Always include the CDK cfn-exec-role pattern so CloudFormation
        # can manage the key during stack create/update (specifically,
        # the post-CreateKey `EnableKeyRotation` call that fires when
        # `enable_key_rotation=True`). Without it the deploy fails with
        # `Access denied for operation 'EnableKeyRotation'` even though
        # the actions are allow-listed in `_ADMIN_ACTIONS`.
        admin_role_arn_patterns = [
            _CDK_CFN_EXEC_ROLE_ARN_PATTERN,
            *list(props.admin_roles),
        ]

        if props.purpose == "encrypt":
            key_spec = kms.KeySpec.SYMMETRIC_DEFAULT
            key_usage = kms.KeyUsage.ENCRYPT_DECRYPT
            principal_actions = _ENCRYPT_PRINCIPAL_ACTIONS
            enable_rotation = True
        else:
            key_spec = kms.KeySpec.ECC_NIST_P256
            key_usage = kms.KeyUsage.SIGN_VERIFY
            principal_actions = _SIGN_VERIFY_PRINCIPAL_ACTIONS
            enable_rotation = False  # rotation only valid for symmetric keys

        # CDK's IPrincipal protocol has a jsii-generated stub mismatch
        # (param `_statement` vs `statement` in add_to_principal_policy),
        # so pyright flags the concrete principal classes as non-conforming.
        # The runtime behaviour is correct; suppress the false positives.
        statements: list[iam.PolicyStatement] = [
            # KMS's policy-lockout safety check (run when CFN calls
            # CreateKey or PutKeyPolicy) requires that the new policy
            # grants someone the unconditional ability to call
            # `kms:PutKeyPolicy` — otherwise the key could become
            # permanently unmodifiable. Adding the MFA condition here
            # fails that check because KMS cannot prove MFA will ever
            # be present at evaluation time, and the deploy fails with
            # `The new key policy will not allow you to update the key
            # policy in the future`.
            #
            # AWS's documented mitigation is to grant the account root
            # `kms:PutKeyPolicy` UNCONDITIONALLY. This isn't a
            # meaningful weakening: the account root is structurally
            # capable of self-granting any IAM permission via the
            # default identity-side admin path; the key-policy lockout
            # protection exists to prevent operators from accidentally
            # locking THEMSELVES out, not to wall off the account root.
            iam.PolicyStatement(
                sid="AccountRootPolicyUpdate",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountPrincipal(account_id)],  # pyright: ignore[reportArgumentType]
                actions=["kms:PutKeyPolicy"],
                resources=["*"],
            ),
            # The MFA-gated break-glass grant is still useful for
            # operators recovering from a Sid mistake — but it lives
            # ALONGSIDE the unconditional PutKeyPolicy above, not as
            # the sole path to key administration.
            iam.PolicyStatement(
                sid="AccountRootForBreakGlass",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountPrincipal(account_id)],  # pyright: ignore[reportArgumentType]
                actions=["kms:Describe*", "kms:Get*", "kms:List*"],
                resources=["*"],
                conditions={"Bool": {"aws:MultiFactorAuthPresent": "true"}},
            ),
        ]

        if props.service_principals:
            statements.append(
                iam.PolicyStatement(
                    sid="ServicePrincipalUse",
                    effect=iam.Effect.ALLOW,
                    principals=[  # pyright: ignore[reportArgumentType]
                        iam.ServicePrincipal(sp) for sp in props.service_principals
                    ],
                    actions=principal_actions,
                    resources=["*"],
                )
            )

        if service_linked_role_principals:
            # Same KMS principal-existence problem as AdminOperations:
            # the role ARN we'd nominally list (e.g. `…:role/gha-cert-ra-sign`)
            # may not yet exist at the moment KMS validates the policy
            # — even though CDK creates it later in the same stack. KMS
            # processes the policy synchronously during CreateKey/
            # PutKeyPolicy and rejects unknown ARNs regardless of stack
            # ordering. Switch to the account root + ArnLike condition
            # so KMS only checks the principal at runtime when the role
            # exists.
            statements.append(
                iam.PolicyStatement(
                    sid="ServiceLinkedRoleUse",
                    effect=iam.Effect.ALLOW,
                    principals=[iam.AccountPrincipal(account_id)],  # pyright: ignore[reportArgumentType]
                    actions=principal_actions,
                    resources=["*"],
                    conditions={
                        "ArnLike": {
                            "aws:PrincipalArn": service_linked_role_principals,
                        },
                    },
                )
            )

        if admin_role_arn_patterns:
            statements.append(
                iam.PolicyStatement(
                    sid="AdminOperations",
                    effect=iam.Effect.ALLOW,
                    # AccountPrincipal is `arn:aws:iam::<account>:root` —
                    # always valid in KMS policies. The actual SSO-role
                    # restriction is enforced by the ArnLike condition
                    # below; KMS evaluates conditions at runtime against
                    # the caller's PrincipalArn, so the access scope is
                    # identical to listing the SSO role ARN directly.
                    principals=[iam.AccountPrincipal(account_id)],  # pyright: ignore[reportArgumentType]
                    actions=_ADMIN_ACTIONS,
                    resources=["*"],
                    conditions={
                        "ArnLike": {
                            "aws:PrincipalArn": admin_role_arn_patterns,
                        },
                    },
                )
            )

        if props.delegate_via_services:
            # Standard "delegate to IAM via service" pattern. The CALLER
            # makes the KMS call (not the service), so the caller's IAM
            # must allow it AND the key policy must allow the caller —
            # this statement is the key-policy side, scoped to calls
            # routed through the named services (so an unrelated path
            # can't elevate to KMS use just because it has IAM grants).
            region = cdk.Stack.of(self).region
            via_services = [
                f"{svc}.{region}.amazonaws.com" for svc in props.delegate_via_services
            ]
            statements.append(
                iam.PolicyStatement(
                    sid="DelegateToIamViaService",
                    effect=iam.Effect.ALLOW,
                    principals=[iam.AccountPrincipal(account_id)],  # pyright: ignore[reportArgumentType]
                    actions=principal_actions,
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            "kms:CallerAccount": account_id,
                            "kms:ViaService": via_services,
                        },
                    },
                )
            )

        statements.extend(
            [
                iam.PolicyStatement(
                    sid="DenyGrantCreationByHumans",
                    effect=iam.Effect.DENY,
                    principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
                    actions=["kms:CreateGrant"],
                    resources=["*"],
                    conditions={"Bool": {"kms:GrantIsForAWSResource": "false"}},
                ),
                iam.PolicyStatement(
                    sid="DenyKeyDeletionWithoutMfa",
                    effect=iam.Effect.DENY,
                    principals=[iam.AnyPrincipal()],  # pyright: ignore[reportArgumentType]
                    actions=["kms:ScheduleKeyDeletion", "kms:DisableKey"],
                    resources=["*"],
                    conditions={
                        "BoolIfExists": {"aws:MultiFactorAuthPresent": "false"},
                    },
                ),
            ]
        )

        self.key = kms.Key(
            self,
            "Key",
            alias=f"alias/cert-ra-{props.key_id}-{props.env}",
            key_spec=key_spec,
            key_usage=key_usage,
            policy=iam.PolicyDocument(statements=statements),
            enable_key_rotation=enable_rotation,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
