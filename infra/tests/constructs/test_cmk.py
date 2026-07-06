# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps


def _synth(props: NarrowKmsCmkProps) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    NarrowKmsCmk(stack, "Cmk", props=props)
    return assertions.Template.from_stack(stack)


def test_encrypt_key_is_symmetric_with_rotation_enabled() -> None:
    template = _synth(
        NarrowKmsCmkProps(
            key_id="rds",
            env="test",
            purpose="encrypt",
            service_principals=["rds.amazonaws.com"],
        )
    )
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeySpec": "SYMMETRIC_DEFAULT",
            "KeyUsage": "ENCRYPT_DECRYPT",
            "EnableKeyRotation": True,
        },
    )


def test_sign_verify_key_is_ecc_with_rotation_disabled() -> None:
    template = _synth(
        NarrowKmsCmkProps(
            key_id="signing",
            env="test",
            purpose="sign-verify",
        )
    )
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeySpec": "ECC_NIST_P256",
            "KeyUsage": "SIGN_VERIFY",
        },
    )
    # Default for EnableKeyRotation is False/absent; we expect it absent.
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    assert key_props.get("EnableKeyRotation", False) is False


def test_account_root_break_glass_requires_mfa() -> None:
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeyPolicy": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "AccountRootForBreakGlass",
                                "Effect": "Allow",
                                "Condition": {
                                    "Bool": {"aws:MultiFactorAuthPresent": "true"}
                                },
                            }
                        ),
                    ]
                ),
            }
        },
    )


def test_account_root_put_key_policy_is_unconditional() -> None:
    """KMS's policy-lockout safety check requires that the new policy
    grants SOMEONE the unconditional ability to call kms:PutKeyPolicy.
    The account root carries that grant in a separate statement with
    no Condition; the MFA-gated AccountRootForBreakGlass statement
    lives alongside it. Without this, KMS rejects key creation with
    "The new key policy will not allow you to update the key policy
    in the future"."""
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeyPolicy": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "AccountRootPolicyUpdate",
                                "Effect": "Allow",
                                "Action": "kms:PutKeyPolicy",
                            }
                        ),
                    ]
                ),
            }
        },
    )
    # Verify the new statement is NOT MFA-gated — if it were, the
    # lockout safety check would still fail.
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    update_stmt = next(
        s
        for s in key_props["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AccountRootPolicyUpdate"
    )
    assert "Condition" not in update_stmt, update_stmt


def test_deny_grant_creation_by_humans_is_present() -> None:
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeyPolicy": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyGrantCreationByHumans",
                                "Effect": "Deny",
                                "Action": "kms:CreateGrant",
                                "Condition": {
                                    "Bool": {"kms:GrantIsForAWSResource": "false"},
                                },
                            }
                        ),
                    ]
                ),
            }
        },
    )


def test_deny_key_deletion_without_mfa_is_present() -> None:
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    template.has_resource_properties(
        "AWS::KMS::Key",
        {
            "KeyPolicy": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyKeyDeletionWithoutMfa",
                                "Effect": "Deny",
                                "Action": ["kms:ScheduleKeyDeletion", "kms:DisableKey"],
                                "Condition": {
                                    "BoolIfExists": {
                                        "aws:MultiFactorAuthPresent": "false"
                                    },
                                },
                            }
                        ),
                    ]
                ),
            }
        },
    )


def test_no_principal_star_allow_statement() -> None:
    template = _synth(
        NarrowKmsCmkProps(
            key_id="signing",
            env="test",
            purpose="sign-verify",
            service_linked_roles=["arn:aws:iam::111111111111:role/gha-cert-ra-sign"],
        )
    )
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    for stmt in key_props["KeyPolicy"]["Statement"]:
        if stmt.get("Effect") == "Allow":
            principal = stmt.get("Principal", {})
            # AccountRoot uses {"AWS": "arn:..."}, service uses {"Service": "..."}.
            # We never want Principal: "*" or {"AWS": "*"} on Allow.
            assert principal != "*"
            assert principal.get("AWS") != "*"


def test_alias_uses_cert_ra_prefix() -> None:
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    template.has_resource_properties(
        "AWS::KMS::Alias",
        {"AliasName": "alias/cert-ra-rds-test"},
    )


def _admin_stmt_json(template: assertions.Template) -> str:
    """Return the AdminOperations policy statement as a JSON string.

    Lets the tests below assert on rendered content without wrestling
    pyright over CDK's dynamically-typed dict outputs.
    """
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    admin_stmt = next(
        s
        for s in key_props["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AdminOperations"
    )
    return json.dumps(admin_stmt, sort_keys=True)


def test_admin_operations_uses_account_principal_not_arn_principal() -> None:
    """KMS rejects ARN principals that contain wildcards or refer to
    SSO roles that don't yet exist (`Policy contains a statement with
    one or more invalid principals`). NarrowKmsCmk works around this
    by using the account root as the principal — which KMS always
    accepts — and enforcing the admin-role restriction via an ArnLike
    condition on `aws:PrincipalArn`. Same effective access, no KMS
    validation tripwire."""
    rendered = _admin_stmt_json(
        _synth(
            NarrowKmsCmkProps(
                key_id="secrets",
                env="test",
                purpose="encrypt",
                admin_roles=[
                    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
                    "AWSReservedSSO_CertRaInstaller_*",
                ],
            )
        )
    )
    # Principal is the account root via AccountPrincipal — CDK renders
    # it as a Fn::Join'd `arn:<partition>:iam::<account>:root`. In the
    # hermetic test env account is concrete (111111111111); in
    # production it'd be `${AWS::AccountId}`. Either way the suffix
    # `:root` is there.
    assert ":root" in rendered, rendered
    # The wildcard SSO pattern must NOT appear in the Principal block
    # (it's restricted to the Condition block instead). Split on the
    # Condition key boundary.
    principal_block = rendered.split('"Condition"', 1)[0]
    assert "AWSReservedSSO_CertRaInstaller_*" not in principal_block, principal_block


def test_admin_operations_arn_like_condition_carries_admin_role_pattern() -> None:
    """The ArnLike condition on `aws:PrincipalArn` is the actual access
    restriction now — verify the wildcard pattern is passed through
    unchanged so SSO roles match at runtime."""
    pattern = (
        "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
        "AWSReservedSSO_CertRaInstaller_*"
    )
    rendered = _admin_stmt_json(
        _synth(
            NarrowKmsCmkProps(
                key_id="secrets",
                env="test",
                purpose="encrypt",
                admin_roles=[pattern],
            )
        )
    )
    # Pattern lives in the ArnLike condition; ArnLike accepts wildcards
    # in any ARN component, including the account position.
    assert '"ArnLike"' in rendered, rendered
    assert '"aws:PrincipalArn"' in rendered, rendered
    assert pattern in rendered, rendered


def test_admin_operations_round_trips_concrete_admin_arn_through_condition() -> None:
    """Concrete admin ARNs (no wildcards) round-trip into the ArnLike
    condition unchanged. The pattern is the access-scoping mechanism
    so callers can mix wildcard SSO patterns with concrete operator
    ARNs in one admin_roles list."""
    concrete = "arn:aws:iam::999999999999:role/CertRaOps"
    rendered = _admin_stmt_json(
        _synth(
            NarrowKmsCmkProps(
                key_id="secrets", env="test", purpose="encrypt", admin_roles=[concrete]
            )
        )
    )
    assert '"ArnLike"' in rendered, rendered
    assert concrete in rendered, rendered


def test_admin_operations_auto_includes_cdk_cfn_exec_role_pattern() -> None:
    """Every CDK-deployed CMK needs the bootstrap cfn-exec-role to be
    able to perform lifecycle ops (specifically, the post-CreateKey
    `EnableKeyRotation` call when `enable_key_rotation=True`).
    NarrowKmsCmk always includes the deterministic cfn-exec-role ARN
    pattern in the AdminOperations ArnLike condition so the deploy
    doesn't fail with `Access denied for operation 'EnableKeyRotation'`.
    """
    rendered = _admin_stmt_json(
        _synth(
            NarrowKmsCmkProps(
                key_id="secrets",
                env="test",
                purpose="encrypt",
                # No admin_roles passed by the caller — the cfn-exec
                # pattern must still show up.
            )
        )
    )
    assert "arn:aws:iam::*:role/cdk-*-cfn-exec-role-*" in rendered, rendered


def test_admin_operations_renders_even_without_caller_admin_roles() -> None:
    """Because the cfn-exec-role pattern is auto-included, the
    AdminOperations statement is emitted even when the caller passes
    `admin_roles=[]`. (Previously the block was gated on a non-empty
    list.)"""
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    sids = {s.get("Sid") for s in key_props["KeyPolicy"]["Statement"]}
    assert "AdminOperations" in sids, sids


def test_admin_operations_includes_kms_delete_for_stack_teardown() -> None:
    """CloudFormation calls `kms:DeleteAlias` when a CMK alias is
    deleted from a stack. Without `kms:Delete*` in the admin actions
    list, every stack teardown that owns a KMS key hits
    `Access denied for operation 'DeleteAlias'` and lands the stack
    in DELETE_FAILED — forcing manual put-key-policy intervention to
    recover. Including Delete* doesn't open a key-destruction path
    because `kms:ScheduleKeyDeletion` is denied unconditionally by
    the cfn-exec boundary's DenyKmsRansom Sid."""
    template = _synth(NarrowKmsCmkProps(key_id="rds", env="test", purpose="encrypt"))
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    admin_stmt = next(
        s
        for s in key_props["KeyPolicy"]["Statement"]
        if s.get("Sid") == "AdminOperations"
    )
    actions = admin_stmt["Action"]
    action_list: list[str] = list(actions) if isinstance(actions, list) else [actions]  # pyright: ignore[reportUnknownArgumentType]
    assert "kms:Delete*" in action_list, action_list
