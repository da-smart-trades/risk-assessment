# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps


def gha_sign_role_name(env: str) -> str:
    """Conventional name for the per-env GHA sign role.

    The sign-CMK key policy needs the role ARN at synth time but the role
    itself is created later in the same stack by GitHubActionsOidcInfra.
    Keeping naming centralised here means both constructs derive the same
    ARN without an explicit handoff. See § Image signing (H1)."""
    return f"gha-cert-ra-sign-{env}"


def cosign_pubkey_param_name(env: str) -> str:
    return f"/cert-ra/{env}/signing/cosign-pubkey"


@dataclass(frozen=True, slots=True)
class ImageSigningInfraProps:
    """Props for ImageSigningInfra. See § Image signing and verification (H1)."""

    env: str
    """Deployment env (`staging` or `prod`). Used to env-suffix the signing
    CMK alias, cosign pubkey SSM param, and the gha-cert-ra-sign role ARN
    that the CMK key policy trusts."""

    admin_role_arns: list[str]
    """Roles allowed to administer the signing CMK (typically Installer ARN)."""

    pubkey_param_name: str | None = None
    """SSM parameter name where the public key is stored after first sign.
    Defaults to `/cert-ra/<env>/signing/cosign-pubkey`."""


class ImageSigningInfra(Construct):
    """KMS sign/verify CMK + cosign-pubkey SSM param shell.

    The signing role (`gha-cert-ra-sign`) is created by
    `GitHubActionsOidcInfra` — this construct only owns the CMK and the
    pubkey parameter. The CMK's key policy grants `kms:Sign` to the
    well-known sign-role ARN (computed from the role name); the actual
    role-side policy granting `kms:Sign` on the CMK is added by
    `GitHubActionsOidcInfra` using the `signing_cmk_arn` prop.
    """

    cmk: NarrowKmsCmk
    pubkey_param: ssm.StringParameter

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: ImageSigningInfraProps,
    ) -> None:
        super().__init__(scope, construct_id)

        account = cdk.Stack.of(self).account
        sign_role_arn = f"arn:aws:iam::{account}:role/{gha_sign_role_name(props.env)}"

        self.cmk = NarrowKmsCmk(
            self,
            "Cmk",
            props=NarrowKmsCmkProps(
                key_id="signing",
                env=props.env,
                purpose="sign-verify",
                service_linked_roles=[sign_role_arn],
                admin_roles=props.admin_role_arns,
            ),
        )

        # Shell parameter; the actual public key value is populated by the
        # gha-cert-ra-sign job's first run (which has ssm:PutParameter on this
        # ARN). The initial value here is a placeholder so the parameter exists
        # at deploy time and IAM scoping by ARN is possible.
        param_name = props.pubkey_param_name or cosign_pubkey_param_name(props.env)
        self.pubkey_param = ssm.StringParameter(
            self,
            "CosignPubkey",
            parameter_name=param_name,
            string_value="__placeholder__cosign_pubkey_pending_first_sign__",
            description=(
                "Cosign public key for cert-ra image verification. Written by "
                "gha-cert-ra-sign on first run; read by upgrade.sh + "
                "BeforeAllowTraffic Lambda hook for `cosign verify`."
            ),
        )

    @property
    def signing_cmk_arn(self) -> str:
        return self.cmk.key.key_arn

    @property
    def pubkey_param_arn(self) -> str:
        return self.pubkey_param.parameter_arn
