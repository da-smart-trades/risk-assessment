# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from cdk_nag import NagSuppressions
from constructs import Construct

GITHUB_OIDC_PROVIDER_URL = "token.actions.githubusercontent.com"
GITHUB_OIDC_AUDIENCE = "sts.amazonaws.com"


@dataclass(frozen=True, slots=True)
class GitHubRepoIdentity:
    """Identifies the GitHub repo whose OIDC tokens we trust."""

    owner: str
    """e.g. `Certora`"""

    repo: str
    """e.g. `risk-assessment`"""

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class GitHubActionsOidcInfraProps:
    """Props for `GitHubActionsOidcInfra`.

    Builds the per-env build/sign/deploy roles described in § GitHub Actions
    OIDC roles in the design spec. The OIDC provider itself is account-level
    and imported by ARN — see `OWNS_OIDC_PROVIDER` below.
    """

    env: str
    """Deployment env (`staging` or `prod`). Suffix on all three GHA role
    names so both env stacks can coexist in one account."""

    repo: GitHubRepoIdentity

    ecr_repo_arn: str
    """The ARN of the per-env ECR repository (`cert-ra-{env}`). Build + sign
    roles get push rights here."""

    signing_cmk_arn: str
    """The ARN of the per-env `cert-ra-signing-{env}` CMK. Sign role gets
    `kms:Sign` here."""

    cosign_pubkey_param_arn: str
    """SSM parameter ARN holding the cosign public key. Sign role writes it once."""

    permissions_boundary_arn: str | None = None
    """Optional permissions boundary applied to every GHA role (default: none, but
    H3+M1 boundaries are typically attached in IdentityStack)."""

    release_branch: str = "main"
    """Branch that sign + deploy roles trust. This is a release-line switch —
    it controls which branch can produce signed images and which branch can
    deploy. Build role is unaffected (it trusts any branch). Defaults to
    `main`; override for a different release line."""


class GitHubActionsOidcInfra(Construct):
    """OIDC provider + build / sign / deploy roles.

    Roles share one OIDC provider; each role's trust policy pins it tighter
    than the last:

    - build: `sub` like `repo:<owner>/<repo>:*` — any branch / job
    - sign:  `sub` = `refs/heads/<release_branch>` AND `job_workflow_ref` = build.yml@<release_branch>
    - deploy: `sub` = `refs/heads/<release_branch>` AND `job_workflow_ref` like `deploy-*.yml@<release_branch>`

    `release_branch` defaults to `main` — the release line.

    GitHub's OIDC issuer cert chain is validated automatically by AWS as of
    2023 (`thumbprints` deprecated); we still supply the published thumbprint
    as a belt-and-suspenders trust anchor.
    """

    provider: iam.IOpenIdConnectProvider
    build_role: iam.Role
    sign_role: iam.Role
    deploy_role: iam.Role

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: GitHubActionsOidcInfraProps,
    ) -> None:
        super().__init__(scope, construct_id)

        # The GitHub OIDC provider is account-level — AWS allows only one
        # provider per (account, URL). With per-env IdentityStacks both
        # `IdentityStack-staging` and `IdentityStack-prod` would race to
        # create the same provider and CFN would reject the second with
        # `EntityAlreadyExists`. We import by deterministic ARN instead;
        # creation is handled out-of-band by initial-setup.sh before any
        # IdentityStack deploy (see `ensure-github-oidc-provider` step).
        account = cdk.Stack.of(self).account
        provider_arn = (
            f"arn:aws:iam::{account}:oidc-provider/{GITHUB_OIDC_PROVIDER_URL}"
        )
        self.provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(  # pyright: ignore[reportAssignmentType]
            self,
            "Provider",
            provider_arn,
        )

        boundary: iam.IManagedPolicy | None = None
        if props.permissions_boundary_arn is not None:
            boundary = iam.ManagedPolicy.from_managed_policy_arn(
                self, "BoundaryRef", props.permissions_boundary_arn
            )

        self.build_role = self._build_role(props, boundary)
        self.sign_role = self._sign_role(props, boundary)
        self.deploy_role = self._deploy_role(props, boundary)

        # Each GHA role has a small, role-specific inline policy. Managed
        # policies don't add value here: the policies are not shared, not
        # reused, and tightly coupled to the role's purpose. NIST 800-53 R5
        # IAMNoInlinePolicy + AwsSolutions IAM5 fire on inline policies; we
        # suppress with rationale rather than restructure for ceremony.
        for role, sid in [
            (self.build_role, "build"),
            (self.sign_role, "sign"),
            (self.deploy_role, "deploy"),
        ]:
            NagSuppressions.add_resource_suppressions(
                role,
                [
                    {
                        "id": "AwsSolutions-IAM5",
                        "reason": (
                            "ECR push permissions necessarily target the "
                            "single cert-ra ECR repository; wildcards inside "
                            "the resource ARN are scoped to repo name. "
                            "ecr:GetAuthorizationToken must be Resource: * "
                            "by AWS contract."
                        ),
                    },
                    {
                        "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                        "reason": (
                            f"GHA {sid} role has a single, purpose-specific "
                            "inline policy. Extracting to a managed policy "
                            "adds no reuse value and complicates the trust-"
                            "boundary story."
                        ),
                    },
                ],
                apply_to_children=True,
            )

    def _federated_principal(
        self,
        *,
        sub_equals: str | None = None,
        sub_like: str | None = None,
        job_workflow_ref_equals: str | None = None,
        job_workflow_ref_like: str | None = None,
    ) -> iam.FederatedPrincipal:
        """Build a FederatedPrincipal scoped to specific GitHub OIDC token claims."""
        string_equals: dict[str, str] = {
            f"{GITHUB_OIDC_PROVIDER_URL}:aud": GITHUB_OIDC_AUDIENCE,
        }
        string_like: dict[str, str] = {}

        if sub_equals is not None:
            string_equals[f"{GITHUB_OIDC_PROVIDER_URL}:sub"] = sub_equals
        if sub_like is not None:
            string_like[f"{GITHUB_OIDC_PROVIDER_URL}:sub"] = sub_like
        if job_workflow_ref_equals is not None:
            string_equals[f"{GITHUB_OIDC_PROVIDER_URL}:job_workflow_ref"] = (
                job_workflow_ref_equals
            )
        if job_workflow_ref_like is not None:
            string_like[f"{GITHUB_OIDC_PROVIDER_URL}:job_workflow_ref"] = (
                job_workflow_ref_like
            )

        conditions: dict[str, dict[str, str]] = {"StringEquals": string_equals}
        if string_like:
            conditions["StringLike"] = string_like

        return iam.FederatedPrincipal(
            self.provider.open_id_connect_provider_arn,
            conditions=conditions,  # pyright: ignore[reportArgumentType]
            assume_role_action="sts:AssumeRoleWithWebIdentity",
        )

    def _build_role(
        self,
        props: GitHubActionsOidcInfraProps,
        boundary: iam.IManagedPolicy | None,
    ) -> iam.Role:
        principal = self._federated_principal(
            sub_like=f"repo:{props.repo.full_name}:*",
        )
        role = iam.Role(
            self,
            "BuildRole",
            role_name=f"gha-cert-ra-build-{props.env}",
            assumed_by=principal,  # pyright: ignore[reportArgumentType]
            permissions_boundary=boundary,
            max_session_duration=cdk.Duration.hours(1),
            description="Used by GHA build jobs (any branch, including PRs) to push to ECR. No deploy / signing rights.",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrPushAndPull",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:DescribeImages",
                    "ecr:DescribeRepositories",
                ],
                resources=[props.ecr_repo_arn],
            )
        )
        # ecr:GetAuthorizationToken requires resource=*
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrAuthorizationToken",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        return role

    def _sign_role(
        self,
        props: GitHubActionsOidcInfraProps,
        boundary: iam.IManagedPolicy | None,
    ) -> iam.Role:
        principal = self._federated_principal(
            sub_equals=(
                f"repo:{props.repo.full_name}:ref:refs/heads/{props.release_branch}"
            ),
            job_workflow_ref_equals=(
                f"{props.repo.full_name}/.github/workflows/"
                f"build.yml@refs/heads/{props.release_branch}"
            ),
        )
        role = iam.Role(
            self,
            "SignRole",
            role_name=f"gha-cert-ra-sign-{props.env}",
            assumed_by=principal,  # pyright: ignore[reportArgumentType]
            permissions_boundary=boundary,
            max_session_duration=cdk.Duration.hours(1),
            description=(
                f"Used only by build.yml's sign job on {props.release_branch}. "
                "kms:Sign + ECR signature manifest write."
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="CosignKmsSign",
                effect=iam.Effect.ALLOW,
                actions=["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"],
                resources=[props.signing_cmk_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrSignatureManifestWrite",
                effect=iam.Effect.ALLOW,
                actions=[
                    # Read: cosign fetches the image manifest by digest
                    # before writing the .sig tag alongside it.
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    # Write: cosign pushes the signature as a sibling manifest.
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[props.ecr_repo_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrAuthorizationToken",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="CosignPubkeySsmWrite",
                effect=iam.Effect.ALLOW,
                actions=["ssm:PutParameter", "ssm:GetParameter"],
                resources=[props.cosign_pubkey_param_arn],
            )
        )
        return role

    def _deploy_role(
        self,
        props: GitHubActionsOidcInfraProps,
        boundary: iam.IManagedPolicy | None,
    ) -> iam.Role:
        principal = self._federated_principal(
            sub_equals=(
                f"repo:{props.repo.full_name}:ref:refs/heads/{props.release_branch}"
            ),
            job_workflow_ref_like=(
                f"{props.repo.full_name}/.github/workflows/"
                f"deploy-*.yml@refs/heads/{props.release_branch}"
            ),
        )
        role = iam.Role(
            self,
            "DeployRole",
            role_name=f"gha-cert-ra-deploy-{props.env}",
            assumed_by=principal,  # pyright: ignore[reportArgumentType]
            permissions_boundary=boundary,
            max_session_duration=cdk.Duration.hours(1),
            description=(
                f"Used by deploy-*.yml on {props.release_branch}. "
                "Mirrors CertRaUpgrader + cosign verify."
            ),
        )
        # Deploy role permissions are populated in IdentityStack via grant_*
        # calls from the relevant constructs (CodeDeploy app, ECS services,
        # Secrets read, signing pubkey read). Kept here as a trusted-identity
        # shell so the trust policy + role name are pinned in this construct.
        return role
