# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps
from cert_ra_infra.constructs.secrets.seeded_secret import (
    SeededSecret,
    SeededSecretProps,
)
from cert_ra_infra.stacks._config import EnvConfig

# Temporal mTLS service identities for which SecretsStack creates empty
# shells. The actual cert + key payloads are written by the Custom Resource
# Lambda in TemporalStack (per § Initial cert population — synchronous
# Custom Resource (B1)).
_TEMPORAL_MTLS_SERVICES = (
    "temporal-frontend",
    "worker-metrics",
    "worker-alerts",
    "internal-worker",
    "maint",
    # App-side client cert. Needed once any route in the Litestar app
    # calls `connect_temporal` (operator-audit fan-out, manual workflow
    # triggers, etc.) — the frontend enforces mTLS, so a plaintext gRPC
    # dial would be refused. Issued the same way as the worker certs by
    # TemporalStack's InitialCertIssuance.
    "app",
)


@dataclass(frozen=True, slots=True)
class SecretsStackProps:
    """Stack-level inputs for SecretsStack."""

    installer_role_arn_pattern: str
    """Used as the admin principal in `cert-ra-secrets-cmk`'s key policy."""


class SecretsStack(Stack):
    """Foundation secrets — `cert-ra-secrets-cmk` plus every SeededSecret
    shell the runtime services need.

    Per § Resource ownership matrix, this stack owns:
    - `cert-ra-secrets-cmk` (one CMK for all SeededSecret encryption)
    - OAuth + RPC + session + email + Sentry shells
    - Temporal mTLS cert shells (populated by TemporalStack later)

    M3 MFA gating is applied to the high-blast-radius secrets:
    - `/cert-ra/{env}/oauth/providers`
    - `/cert-ra/{env}/app/session-secret`

    RPC keys, email key, Sentry DSN, and Temporal mTLS shells do not
    require MFA writes because their rotation paths are either annual
    calendar reminders (RPC, email) or fully automated via service
    identity Lambdas (Temporal mTLS).
    """

    secrets_cmk: NarrowKmsCmk
    oauth_providers: SeededSecret
    rpc_providers: SeededSecret
    session_secret: SeededSecret
    email_api_key: SeededSecret
    sentry_dsn: SeededSecret
    anthropic_api_key: SeededSecret
    openai_api_key: SeededSecret
    the_graph_api_key: SeededSecret
    dune_api_key: SeededSecret
    superuser: SeededSecret
    temporal_mtls_secrets: dict[str, SeededSecret]

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        secrets_props: SecretsStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        env = env_config.env

        self.secrets_cmk = NarrowKmsCmk(
            self,
            "SecretsCmk",
            props=NarrowKmsCmkProps(
                key_id="secrets",
                env=env,
                purpose="encrypt",
                service_principals=["secretsmanager.amazonaws.com"],
                admin_roles=[secrets_props.installer_role_arn_pattern],
            ),
        )

        # OAuth provider client IDs + secrets. M3-gated: rewriting these
        # hijacks the IdP round-trip.
        self.oauth_providers = SeededSecret(
            self,
            "OauthProviders",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/oauth/providers",
                description="OAuth client IDs + secrets for Google / GitHub / Microsoft",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
                require_mfa_for_writes=True,
                installer_role_arn_pattern=secrets_props.installer_role_arn_pattern,
            ),
        )

        # RPC provider API keys (Alchemy, Infura, etc.). Annual rotation.
        self.rpc_providers = SeededSecret(
            self,
            "RpcProviders",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/rpc/providers",
                description="RPC provider URLs / API keys for ETH / Solana / Polygon / Avalanche",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Litestar session secret. M3-gated: rewriting logs everyone out;
        # an attacker with this could re-sign session tokens.
        self.session_secret = SeededSecret(
            self,
            "SessionSecret",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/app/session-secret",
                description="Litestar session signing key (32+ random bytes)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
                require_mfa_for_writes=True,
                installer_role_arn_pattern=secrets_props.installer_role_arn_pattern,
            ),
        )

        self.email_api_key = SeededSecret(
            self,
            "EmailApiKey",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/email/resend-api-key",
                description="Resend API key for transactional email",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        self.sentry_dsn = SeededSecret(
            self,
            "SentryDsn",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/sentry/dsn",
                description="Sentry DSN (write-only; rotation on suspected compromise only)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Anthropic API key — LLM backend. Read by both app and worker
        # containers. No MFA gate; rotation cadence is annual or on
        # suspected leak (same as RPC providers).
        self.anthropic_api_key = SeededSecret(
            self,
            "AnthropicApiKey",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/anthropic/api-key",
                description="Anthropic LLM API key (ANTHROPIC_API_KEY)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # OpenAI API key — read as OPENAI_API_KEY by the OpenAI SDK.
        self.openai_api_key = SeededSecret(
            self,
            "OpenAiApiKey",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/openai/api-key",
                description="OpenAI API key (OPENAI_API_KEY)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # The Graph API key — external data provider. Read by both app
        # and worker containers.
        self.the_graph_api_key = SeededSecret(
            self,
            "TheGraphApiKey",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/the-graph/api-key",
                description="The Graph external API key (THE_GRAPH_API_KEY)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Dune Analytics API key — read as CERT_RA_DUNE_API_KEY by
        # DuneSettings (env_prefix="cert_ra_dune_").
        self.dune_api_key = SeededSecret(
            self,
            "DuneApiKey",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/dune/api-key",
                description="Dune Analytics API key (CERT_RA_DUNE_API_KEY)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Bootstrap superuser credentials. Injected into the app container
        # as CERT_RA_SUPERUSER_EMAIL + CERT_RA_SUPERUSER_PASSWORD so the
        # _ensure_superuser startup hook creates the first admin account.
        # The hook is idempotent — once a superuser exists the env vars are
        # ignored. No MFA gate: the initial seed must work from the Installer
        # SSO session, same as the RPC / email / Sentry secrets.
        self.superuser = SeededSecret(
            self,
            "Superuser",
            props=SeededSecretProps(
                secret_name=f"/cert-ra/{env}/app/superuser",
                description="Bootstrap superuser email + password (JSON: email, password)",
                encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
            ),
        )

        # Temporal mTLS shells. Created empty; populated by TemporalStack's
        # InitialCertIssuance Custom Resource (B1) during stack create.
        self.temporal_mtls_secrets = {}
        for service in _TEMPORAL_MTLS_SERVICES:
            construct_id_suffix = service.replace("-", " ").title().replace(" ", "")
            self.temporal_mtls_secrets[service] = SeededSecret(
                self,
                f"TemporalMtls{construct_id_suffix}",
                props=SeededSecretProps(
                    secret_name=f"/cert-ra/{env}/temporal/mtls/{service}",
                    description=f"Temporal mTLS cert + key for {service} (populated by TemporalStack)",
                    encryption_key=self.secrets_cmk.key,  # pyright: ignore[reportArgumentType]
                ),
            )

        # Outputs for downstream stacks + operator scripts.
        cdk.CfnOutput(
            self,
            "SecretsCmkArn",
            value=self.secrets_cmk.key.key_arn,
            export_name=f"{self.stack_name}-SecretsCmkArn",
        )
        cdk.CfnOutput(
            self,
            "OauthProvidersSecretArn",
            value=self.oauth_providers.secret_arn,
            export_name=f"{self.stack_name}-OauthProvidersSecretArn",
        )
        cdk.CfnOutput(
            self,
            "RpcProvidersSecretArn",
            value=self.rpc_providers.secret_arn,
            export_name=f"{self.stack_name}-RpcProvidersSecretArn",
        )
        cdk.CfnOutput(
            self,
            "SessionSecretArn",
            value=self.session_secret.secret_arn,
            export_name=f"{self.stack_name}-SessionSecretArn",
        )
        cdk.CfnOutput(
            self,
            "AnthropicApiKeyArn",
            value=self.anthropic_api_key.secret_arn,
            export_name=f"{self.stack_name}-AnthropicApiKeyArn",
        )
        cdk.CfnOutput(
            self,
            "TheGraphApiKeyArn",
            value=self.the_graph_api_key.secret_arn,
            export_name=f"{self.stack_name}-TheGraphApiKeyArn",
        )
        cdk.CfnOutput(
            self,
            "OpenAiApiKeyArn",
            value=self.openai_api_key.secret_arn,
            export_name=f"{self.stack_name}-OpenAiApiKeyArn",
        )
        cdk.CfnOutput(
            self,
            "DuneApiKeyArn",
            value=self.dune_api_key.secret_arn,
            export_name=f"{self.stack_name}-DuneApiKeyArn",
        )
        cdk.CfnOutput(
            self,
            "SuperuserSecretArn",
            value=self.superuser.secret_arn,
            export_name=f"{self.stack_name}-SuperuserSecretArn",
        )
        for service, seeded in self.temporal_mtls_secrets.items():
            construct_id_suffix = service.replace("-", " ").title().replace(" ", "")
            cdk.CfnOutput(
                self,
                f"TemporalMtls{construct_id_suffix}Arn",
                value=seeded.secret_arn,
                export_name=(f"{self.stack_name}-TemporalMtls{construct_id_suffix}Arn"),
            )
