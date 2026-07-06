# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.gha_oidc_role import GitHubRepoIdentity
from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.identity import IdentityStack, IdentityStackProps

_REPO = GitHubRepoIdentity(owner="Certora", repo="risk-assessment")
_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _synth_stack(env_name: str = "staging") -> assertions.Template:
    app = cdk.App()
    cfg = load_env(env_name)
    env = cdk.Environment(account="111111111111", region=cfg.region)
    stack = IdentityStack(
        app,
        f"CertRa-IdentityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        identity_props=IdentityStackProps(
            github_repo=_REPO,
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_stack_creates_env_suffixed_cfn_exec_boundary_policy() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {"ManagedPolicyName": "cert-ra-cfn-exec-boundary-staging"},
    )


def test_stack_creates_env_suffixed_ecr_repo() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::ECR::Repository",
        {"RepositoryName": "cert-ra-staging", "ImageTagMutability": "IMMUTABLE"},
    )


def test_stack_creates_signing_cmk_with_sign_verify_usage() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::KMS::Key",
        {"KeyUsage": "SIGN_VERIFY", "KeySpec": "ECC_NIST_P256"},
    )


def test_stack_creates_all_three_env_suffixed_gha_roles() -> None:
    template = _synth_stack()
    for role_name in (
        "gha-cert-ra-build-staging",
        "gha-cert-ra-sign-staging",
        "gha-cert-ra-deploy-staging",
    ):
        template.has_resource_properties("AWS::IAM::Role", {"RoleName": role_name})


def test_stack_exports_cfn_outputs_for_consumption() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    required_outputs = {
        "CfnExecBoundaryName",
        "EcrRepoArn",
        "EcrRepoUri",
        "SigningCmkArn",
        "CosignPubkeyParamArn",
        "GhaBuildRoleArn",
        "GhaSignRoleArn",
        "GhaDeployRoleArn",
    }
    assert required_outputs.issubset(set(outputs.keys()))


def test_stack_attaches_boundary_to_gha_sign_role() -> None:
    """sign role's `PermissionsBoundary` must reference the cfn-exec-boundary policy."""
    template = _synth_stack()
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": "gha-cert-ra-sign-staging"}}
    )
    (sign_role,) = roles.values()
    assert "PermissionsBoundary" in sign_role["Properties"]
