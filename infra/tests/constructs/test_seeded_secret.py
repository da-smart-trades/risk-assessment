# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.secrets.seeded_secret import (
    PLACEHOLDER_VALUE,
    SeededSecret,
    SeededSecretProps,
)


def _synth(
    *,
    secret_name: str = "/cert-ra/staging/oauth/providers",
    require_mfa_for_writes: bool = False,
    placeholder_value: str = PLACEHOLDER_VALUE,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    cmk = NarrowKmsCmk(
        stack,
        "Cmk",
        props=NarrowKmsCmkProps(
            key_id="secrets",
            env="test",
            purpose="encrypt",
            service_principals=["secretsmanager.amazonaws.com"],
        ),
    )
    SeededSecret(
        stack,
        "Secret",
        props=SeededSecretProps(
            secret_name=secret_name,
            description="test secret",
            encryption_key=cmk.key,  # pyright: ignore[reportArgumentType]
            placeholder_value=placeholder_value,
            require_mfa_for_writes=require_mfa_for_writes,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_secret_name_is_set() -> None:
    template = _synth(secret_name="/cert-ra/prod/app/session-secret")
    template.has_resource_properties(
        "AWS::SecretsManager::Secret",
        {"Name": "/cert-ra/prod/app/session-secret"},
    )


def test_secret_uses_provided_cmk() -> None:
    template = _synth()
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    (secret,) = secrets.values()
    assert "KmsKeyId" in secret["Properties"]


def test_secret_value_is_placeholder_by_default() -> None:
    template = _synth()
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    (secret,) = secrets.values()
    assert secret["Properties"]["SecretString"] == PLACEHOLDER_VALUE


def test_custom_placeholder_value_is_used() -> None:
    template = _synth(placeholder_value="__CUSTOM_MARKER__")
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    (secret,) = secrets.values()
    assert secret["Properties"]["SecretString"] == "__CUSTOM_MARKER__"


def test_secret_retains_on_stack_delete() -> None:
    template = _synth()
    secrets = template.find_resources("AWS::SecretsManager::Secret")
    (secret,) = secrets.values()
    assert secret.get("DeletionPolicy") == "Retain"


def test_no_mfa_policy_when_require_mfa_for_writes_is_false() -> None:
    template = _synth(require_mfa_for_writes=False)
    template.resource_count_is("AWS::SecretsManager::ResourcePolicy", 0)


def test_mfa_policy_present_when_require_mfa_for_writes_is_true() -> None:
    """M3: writes denied unless aws:MultiFactorAuthPresent is true."""
    template = _synth(require_mfa_for_writes=True)
    template.resource_count_is("AWS::SecretsManager::ResourcePolicy", 1)
    template.has_resource_properties(
        "AWS::SecretsManager::ResourcePolicy",
        {
            "ResourcePolicy": {
                "Statement": assertions.Match.array_with(
                    [
                        assertions.Match.object_like(
                            {
                                "Sid": "DenyMutationsWithoutMfa",
                                "Effect": "Deny",
                                "Condition": {
                                    "BoolIfExists": {
                                        "aws:MultiFactorAuthPresent": "false",
                                    },
                                },
                            }
                        ),
                    ]
                ),
            },
        },
    )


def test_mfa_policy_denies_all_write_actions() -> None:
    """The deny list must cover Put, Update, Delete, Restore, Rotate, CancelRotate."""
    template = _synth(require_mfa_for_writes=True)
    policies = template.find_resources("AWS::SecretsManager::ResourcePolicy")
    (policy,) = policies.values()
    statements = policy["Properties"]["ResourcePolicy"]["Statement"]
    deny_stmt = next(s for s in statements if s.get("Sid") == "DenyMutationsWithoutMfa")
    actions = set(deny_stmt["Action"])
    expected = {
        "secretsmanager:PutSecretValue",
        "secretsmanager:UpdateSecret",
        "secretsmanager:DeleteSecret",
        "secretsmanager:RestoreSecret",
        "secretsmanager:CancelRotateSecret",
        "secretsmanager:RotateSecret",
    }
    assert expected.issubset(actions), f"Missing actions: {expected - actions}"


def test_mfa_policy_uses_bool_if_exists_for_service_identity_compat() -> None:
    """BoolIfExists allows service identities (no MFA claim) to write; only humans need MFA."""
    template = _synth(require_mfa_for_writes=True)
    policies = template.find_resources("AWS::SecretsManager::ResourcePolicy")
    (policy,) = policies.values()
    statements = policy["Properties"]["ResourcePolicy"]["Statement"]
    deny_stmt = next(s for s in statements if s.get("Sid") == "DenyMutationsWithoutMfa")
    condition = deny_stmt["Condition"]
    assert "BoolIfExists" in condition
    assert "Bool" not in condition, (
        "Must use BoolIfExists, not Bool — see § Secrets rotation M3"
    )
