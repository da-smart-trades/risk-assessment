# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.identity.cfn_exec_boundary import (
    CfnExecBoundary,
    CfnExecBoundaryProps,
)
from cert_ra_infra.constructs.identity.ecr_repo import (
    CertRaEcrRepo,
    CertRaEcrRepoProps,
)
from cert_ra_infra.constructs.identity.gha_oidc_role import (
    GitHubActionsOidcInfra,
    GitHubActionsOidcInfraProps,
    GitHubRepoIdentity,
)
from cert_ra_infra.constructs.identity.signing import (
    ImageSigningInfra,
    ImageSigningInfraProps,
)
from cert_ra_infra.stacks._config import EnvConfig


@dataclass(frozen=True, slots=True)
class IdentityStackProps:
    """Stack-level inputs for IdentityStack.

    Installer role ARNs are wired in from outside (Identity Center
    provisions the AWSReservedSSO_CertRaInstaller_* roles on first SSO
    sign-in; the ARN pattern is stable but the suffix is generated). For
    initial deploy we accept a wildcard-shape via `installer_role_arn_pattern`
    and the construct uses that as the admin principal in KMS key
    policies. The build/sign/deploy GHA roles are created here.
    """

    github_repo: GitHubRepoIdentity
    installer_role_arn_pattern: str
    """e.g. `arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_CertRaInstaller_*`"""


class IdentityStack(Stack):
    """Foundation IAM + image-signing + ECR.

    Deployed FIRST during initial-setup (before `cdk bootstrap`), because
    `cdk bootstrap --custom-permissions-boundary cert-ra-cfn-exec-boundary`
    needs the boundary policy to already exist. See § Initial setup flow
    step 3a in the design spec.
    """

    cfn_exec_boundary: CfnExecBoundary
    ecr: CertRaEcrRepo
    image_signing: ImageSigningInfra
    gha: GitHubActionsOidcInfra

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        identity_props: IdentityStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        self.cfn_exec_boundary = CfnExecBoundary(
            self,
            "CfnExecBoundary",
            props=CfnExecBoundaryProps(env=env_config.env),
        )

        self.ecr = CertRaEcrRepo(
            self,
            "Ecr",
            props=CertRaEcrRepoProps(
                env=env_config.env,
                admin_role_arns=[identity_props.installer_role_arn_pattern],
            ),
        )

        self.image_signing = ImageSigningInfra(
            self,
            "ImageSigning",
            props=ImageSigningInfraProps(
                env=env_config.env,
                admin_role_arns=[identity_props.installer_role_arn_pattern],
            ),
        )

        self.gha = GitHubActionsOidcInfra(
            self,
            "Gha",
            props=GitHubActionsOidcInfraProps(
                env=env_config.env,
                repo=identity_props.github_repo,
                ecr_repo_arn=self.ecr.repository_arn,
                signing_cmk_arn=self.image_signing.signing_cmk_arn,
                cosign_pubkey_param_arn=self.image_signing.pubkey_param_arn,
                permissions_boundary_arn=self.cfn_exec_boundary.policy.managed_policy_arn,
            ),
        )

        # Outputs consumed by initial-setup.sh and downstream stacks.
        cdk.CfnOutput(
            self,
            "CfnExecBoundaryName",
            value=self.cfn_exec_boundary.policy.managed_policy_name,
            export_name=f"{self.stack_name}-CfnExecBoundaryName",
        )
        cdk.CfnOutput(
            self,
            "EcrRepoArn",
            value=self.ecr.repository_arn,
            export_name=f"{self.stack_name}-EcrRepoArn",
        )
        cdk.CfnOutput(
            self,
            "EcrRepoUri",
            value=self.ecr.repository.repository_uri,
            export_name=f"{self.stack_name}-EcrRepoUri",
        )
        cdk.CfnOutput(
            self,
            "SigningCmkArn",
            value=self.image_signing.signing_cmk_arn,
            export_name=f"{self.stack_name}-SigningCmkArn",
        )
        cdk.CfnOutput(
            self,
            "CosignPubkeyParamArn",
            value=self.image_signing.pubkey_param_arn,
            export_name=f"{self.stack_name}-CosignPubkeyParamArn",
        )
        cdk.CfnOutput(
            self,
            "GhaBuildRoleArn",
            value=self.gha.build_role.role_arn,
            export_name=f"{self.stack_name}-GhaBuildRoleArn",
        )
        cdk.CfnOutput(
            self,
            "GhaSignRoleArn",
            value=self.gha.sign_role.role_arn,
            export_name=f"{self.stack_name}-GhaSignRoleArn",
        )
        cdk.CfnOutput(
            self,
            "GhaDeployRoleArn",
            value=self.gha.deploy_role.role_arn,
            export_name=f"{self.stack_name}-GhaDeployRoleArn",
        )
