# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.signing import (
    ImageSigningInfra,
    ImageSigningInfraProps,
    cosign_pubkey_param_name,
    gha_sign_role_name,
)

ADMIN_ROLE_ARN = "arn:aws:iam::111111111111:role/AWSReservedSSO_CertRaInstaller_xxxx"
TEST_ENV = "test"


def _synth(
    *,
    env: str = TEST_ENV,
    pubkey_param_name: str | None = None,
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    ImageSigningInfra(
        stack,
        "Sign",
        props=ImageSigningInfraProps(
            env=env,
            admin_role_arns=[ADMIN_ROLE_ARN],
            pubkey_param_name=pubkey_param_name,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_sign_verify_cmk() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::KMS::Key",
        {"KeySpec": "ECC_NIST_P256", "KeyUsage": "SIGN_VERIFY"},
    )


def test_signing_cmk_alias_includes_env_suffix() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::KMS::Alias", {"AliasName": f"alias/cert-ra-signing-{TEST_ENV}"}
    )


def test_signing_cmk_key_policy_carries_gha_sign_role_via_arn_like_condition() -> None:
    """The gha-cert-ra-sign-<env> role ARN lives in the ArnLike condition
    on `aws:PrincipalArn`, not as the literal Principal — KMS doesn't need
    to validate the role exists at policy-create time (IdentityStack
    creates the role AFTER the CMK in the same template)."""
    template = _synth()
    expected_role_name = gha_sign_role_name(TEST_ENV)
    expected_arn = f"arn:aws:iam::111111111111:role/{expected_role_name}"
    keys = template.find_resources("AWS::KMS::Key")
    (key_props,) = (k["Properties"] for k in keys.values())
    sl_use = [
        s
        for s in key_props["KeyPolicy"]["Statement"]
        if s.get("Sid") == "ServiceLinkedRoleUse"
    ]
    assert len(sl_use) == 1
    stmt_json = json.dumps(sl_use[0], sort_keys=True)
    assert ':root"' in stmt_json, stmt_json
    assert '"ArnLike"' in stmt_json, stmt_json
    assert expected_role_name in stmt_json, stmt_json
    assert expected_arn.endswith(expected_role_name)


def test_pubkey_param_name_defaults_to_per_env_path() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::SSM::Parameter",
        {"Name": cosign_pubkey_param_name(TEST_ENV), "Type": "String"},
    )


def test_pubkey_param_value_is_a_placeholder() -> None:
    template = _synth()
    params = template.find_resources("AWS::SSM::Parameter")
    (param_props,) = (p["Properties"] for p in params.values())
    assert "placeholder" in param_props["Value"].lower()


def test_pubkey_param_name_is_overridable() -> None:
    template = _synth(pubkey_param_name="/cert-ra/staging/signing/pubkey")
    template.has_resource_properties(
        "AWS::SSM::Parameter",
        {"Name": "/cert-ra/staging/signing/pubkey"},
    )
