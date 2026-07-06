# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.gha_oidc_role import (
    GITHUB_OIDC_PROVIDER_URL,
    GitHubActionsOidcInfra,
    GitHubActionsOidcInfraProps,
    GitHubRepoIdentity,
)

REPO = GitHubRepoIdentity(owner="Certora", repo="risk-assessment")
TEST_ENV = "test"
ECR_ARN = f"arn:aws:ecr:us-east-1:111111111111:repository/cert-ra-{TEST_ENV}"
SIGNING_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)
PUBKEY_PARAM_ARN = f"arn:aws:ssm:us-east-1:111111111111:parameter/cert-ra/{TEST_ENV}/signing/cosign-pubkey"

BUILD_ROLE = f"gha-cert-ra-build-{TEST_ENV}"
SIGN_ROLE = f"gha-cert-ra-sign-{TEST_ENV}"
DEPLOY_ROLE = f"gha-cert-ra-deploy-{TEST_ENV}"


def _synth(
    *,
    env: str = TEST_ENV,
    permissions_boundary_arn: str | None = None,
    release_branch: str = "main",
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    GitHubActionsOidcInfra(
        stack,
        "Gha",
        props=GitHubActionsOidcInfraProps(
            env=env,
            repo=REPO,
            ecr_repo_arn=ECR_ARN,
            signing_cmk_arn=SIGNING_CMK_ARN,
            cosign_pubkey_param_arn=PUBKEY_PARAM_ARN,
            permissions_boundary_arn=permissions_boundary_arn,
            release_branch=release_branch,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_release_branch_override_changes_sign_and_deploy_trust_pins() -> None:
    """Switching `release_branch=main` must flip both sign and deploy
    roles' trust policies to that branch; build role is unaffected."""
    template = _synth(release_branch="main")
    sign = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": SIGN_ROLE}}
    )
    (sign_role,) = sign.values()
    sign_cond = sign_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Condition"
    ]
    sub_key = f"{GITHUB_OIDC_PROVIDER_URL}:sub"
    jwr_key = f"{GITHUB_OIDC_PROVIDER_URL}:job_workflow_ref"
    assert "refs/heads/main" in sign_cond["StringEquals"][sub_key]
    assert "build.yml@refs/heads/main" in sign_cond["StringEquals"][jwr_key]

    deploy = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": DEPLOY_ROLE}}
    )
    (deploy_role,) = deploy.values()
    deploy_cond = deploy_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Condition"
    ]
    assert "refs/heads/main" in deploy_cond["StringEquals"][sub_key]
    assert "deploy-*.yml@refs/heads/main" in deploy_cond["StringLike"][jwr_key]


def test_oidc_provider_is_imported_not_created() -> None:
    """Account-level OIDC provider is shared across envs; this construct
    imports it by deterministic ARN rather than creating a duplicate."""
    template = _synth()
    template.resource_count_is("Custom::AWSCDKOpenIdConnectProvider", 0)


def test_creates_three_roles_with_env_suffixed_names() -> None:
    template = _synth()
    for role_name in (BUILD_ROLE, SIGN_ROLE, DEPLOY_ROLE):
        template.has_resource_properties("AWS::IAM::Role", {"RoleName": role_name})


def test_all_gha_roles_have_one_hour_max_session() -> None:
    template = _synth()
    for role_name in (BUILD_ROLE, SIGN_ROLE, DEPLOY_ROLE):
        template.has_resource_properties(
            "AWS::IAM::Role",
            {"RoleName": role_name, "MaxSessionDuration": 3600},
        )


def test_build_role_trust_uses_sub_wildcard() -> None:
    """Build role trusts any branch / PR — sub like repo:owner/repo:*."""
    template = _synth()
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": BUILD_ROLE}}
    )
    (build_role,) = roles.values()
    cond = build_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Condition"
    ]
    sub_key = f"{GITHUB_OIDC_PROVIDER_URL}:sub"
    assert cond["StringLike"][sub_key] == "repo:Certora/risk-assessment:*"


def test_sign_role_trust_pins_job_workflow_ref_to_build_yml() -> None:
    template = _synth()
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": SIGN_ROLE}}
    )
    (sign_role,) = roles.values()
    cond = sign_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Condition"
    ]
    string_equals = cond["StringEquals"]
    sub_key = f"{GITHUB_OIDC_PROVIDER_URL}:sub"
    jwr_key = f"{GITHUB_OIDC_PROVIDER_URL}:job_workflow_ref"
    assert (
        string_equals[sub_key]
        == "repo:Certora/risk-assessment:ref:refs/heads/main"
    )
    assert (
        string_equals[jwr_key]
        == "Certora/risk-assessment/.github/workflows/build.yml@refs/heads/main"
    )
    assert "StringLike" not in cond


def test_deploy_role_trust_uses_job_workflow_ref_like_deploy_glob() -> None:
    template = _synth()
    roles = template.find_resources(
        "AWS::IAM::Role", {"Properties": {"RoleName": DEPLOY_ROLE}}
    )
    (deploy_role,) = roles.values()
    cond = deploy_role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
        "Condition"
    ]
    sub_key = f"{GITHUB_OIDC_PROVIDER_URL}:sub"
    jwr_key = f"{GITHUB_OIDC_PROVIDER_URL}:job_workflow_ref"
    assert (
        cond["StringEquals"][sub_key]
        == "repo:Certora/risk-assessment:ref:refs/heads/main"
    )
    assert (
        cond["StringLike"][jwr_key]
        == "Certora/risk-assessment/.github/workflows/deploy-*.yml@refs/heads/main"
    )


def test_build_role_has_ecr_push_but_no_signing() -> None:
    """Build role can push images; cannot kms:Sign."""
    template = _synth()
    policies = template.find_resources(
        "AWS::IAM::Policy",
        {
            "Properties": {
                "Roles": [
                    assertions.Match.object_like(
                        {"Ref": assertions.Match.string_like_regexp(".*BuildRole.*")}
                    )
                ]
            }
        },
    )
    assert len(policies) == 1, (
        "Expected exactly one inline policy attached to BuildRole"
    )
    (policy,) = policies.values()
    statements: list[dict[str, object]] = policy["Properties"]["PolicyDocument"][
        "Statement"
    ]
    actions_seen: set[str] = set()
    for stmt in statements:
        action = stmt.get("Action")
        if isinstance(action, list):
            for a in action:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(a, str):
                    actions_seen.add(a)
        elif isinstance(action, str):
            actions_seen.add(action)
    assert "ecr:PutImage" in actions_seen
    assert "kms:Sign" not in actions_seen


def test_sign_role_has_kms_sign_scoped_to_signing_cmk() -> None:
    template = _synth()
    policies = template.find_resources(
        "AWS::IAM::Policy",
        {
            "Properties": {
                "Roles": [
                    assertions.Match.object_like(
                        {"Ref": assertions.Match.string_like_regexp(".*SignRole.*")}
                    )
                ]
            }
        },
    )
    (policy,) = policies.values()
    sign_stmts = [
        s
        for s in policy["Properties"]["PolicyDocument"]["Statement"]
        if s.get("Sid") == "CosignKmsSign"
    ]
    assert len(sign_stmts) == 1
    stmt = sign_stmts[0]
    assert "kms:Sign" in stmt["Action"]
    assert stmt["Resource"] == SIGNING_CMK_ARN


def test_permissions_boundary_is_applied_when_provided() -> None:
    boundary_arn = (
        f"arn:aws:iam::111111111111:policy/cert-ra-cfn-exec-boundary-{TEST_ENV}"
    )
    template = _synth(permissions_boundary_arn=boundary_arn)
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "RoleName": SIGN_ROLE,
            "PermissionsBoundary": boundary_arn,
        },
    )
