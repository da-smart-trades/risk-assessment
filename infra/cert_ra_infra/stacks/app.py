# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_codedeploy as codedeploy
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from constructs import Construct

from cert_ra_infra.constructs.app.blue_green_deployment import (
    BlueGreenDeployment,
    BlueGreenDeploymentProps,
)
from cert_ra_infra.constructs.app.litestar_service import (
    DEFAULT_DESIRED_COUNT,
    AppSecretInjection,
    LitestarService,
    LitestarServiceProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack
from cert_ra_infra.stacks.dns import DnsStack
from cert_ra_infra.stacks.identity import IdentityStack
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import ObservabilityStack
from cert_ra_infra.stacks.secrets import SecretsStack

# Default image tag when no override is passed. Operators run
# `cdk deploy CertRa-AppStack-${ENV} -c app_image_tag=sha-abc123` to
# pin a known-good image. The default is `latest` for steady-state
# recovery / re-deploys; CI's upgrade.sh pins each deploy to an
# immutable sha tag, and initial-setup.sh sets `bootstrap=True` (see
# `AppStackProps.bootstrap`) — image_tag is independent of bootstrap
# state, so we never assume "image_tag == latest" means "this is a
# fresh install" again. That conflation reset prod desired_count from
# 2 to 0 on every recovery `cdk deploy` until we surfaced it during
# the 2026-06-06 incident.
DEFAULT_IMAGE_TAG = "latest"


def _empty_str_dict() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class AppStackProps:
    """Stack-level inputs for AppStack.

    Cross-stack refs are passed as full stack objects when CDK can
    safely thread them, and as bare ARN strings (`*_arn: str`) when
    threading the object would create a dep cycle (same pattern as
    TemporalStack — see its docstring for the cycle's mechanics).
    """

    network: NetworkStack
    dns: DnsStack
    data: DataStack
    secrets: SecretsStack
    observability: ObservabilityStack
    identity: IdentityStack
    temporal: TemporalStack

    image_tag: str = DEFAULT_IMAGE_TAG
    """Image tag to deploy. Defaults to `latest`; CI overrides with
    `sha-<git_sha>` per the ECR repo's IMMUTABLE tag policy."""

    bootstrap: bool = False
    """When True, register the ECS service with `desired_count=0` so
    CFN doesn't wait for tasks to stabilise on what might be a
    not-yet-pushed image. Used by `initial-setup.sh` for the first
    AppStack create (no real image exists yet, no traffic-shift run by
    upgrade.sh yet). Every other deploy path — recovery scripts, CI's
    upgrade.sh, manual `cdk deploy` — should leave this as False so
    the service stays at the construct's `DEFAULT_DESIRED_COUNT`.

    Why a separate flag instead of inferring from `image_tag`: the old
    heuristic was `desired_count=0 if image_tag == "latest"`, which
    silently reset prod to 0/0 every time a recovery script re-deployed
    AppStack without setting `CDK_APP_IMAGE_TAG`. The bootstrap state
    is now explicit so it can't be triggered accidentally."""

    extra_env: dict[str, str] = field(default_factory=_empty_str_dict)
    """Plain (non-secret) env vars layered on top of LitestarService's
    defaults. Used for things like `LOG_LEVEL=info`."""


class AppStack(Stack):
    """Public Litestar application stack.

    After AppStack PR 2:
    - `LitestarService` (PR 1) — Fargate service, ALB target groups,
      and listener rules. Service runs with
      `deployment_controller=CODE_DEPLOY` so CDK only registers task
      definition revisions.
    - `BlueGreenDeployment` (PR 2) — CodeDeploy ECS application +
      deployment group wired to the service, both target groups,
      both listeners, plus auto-rollback alarms and
      `BeforeAllowTraffic` / `AfterAllowTraffic` Lambda hooks. In
      staging the deploy is linear 10%-every-1-minute; prod is
      canary 10% / 5 minutes.

    Still pending:
    - Worker-side Temporal mTLS client cert injection (handled in
      WorkersStack analogue).
    """

    litestar: LitestarService
    blue_green: BlueGreenDeployment
    alb_alias_record: route53.ARecord
    www_alias_record: route53.ARecord

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        app_props: AppStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        # Build the secret-injection list from SecretsStack's named
        # outputs. Each entry maps directly to the env var name that
        # pydantic-settings (or the external SDK) reads — no JSON blob
        # expansion step exists at runtime, so the env var must match
        # the field name the app consumes.
        secret_injections = [
            # AppSettings (env_prefix="cert_ra_app_") → CERT_RA_APP_SECRET_KEY
            AppSecretInjection(
                env_var="CERT_RA_APP_SECRET_KEY",
                secret_arn=app_props.secrets.session_secret.secret_arn,
            ),
            # AppSettings OAuth fields (env_prefix="cert_ra_app_")
            AppSecretInjection(
                env_var="CERT_RA_APP_GOOGLE_OAUTH2_CLIENT_ID",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="google.client_id",
            ),
            AppSecretInjection(
                env_var="CERT_RA_APP_GOOGLE_OAUTH2_CLIENT_SECRET",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="google.client_secret",
            ),
            AppSecretInjection(
                env_var="CERT_RA_APP_GITHUB_OAUTH2_CLIENT_ID",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="github.client_id",
            ),
            AppSecretInjection(
                env_var="CERT_RA_APP_GITHUB_OAUTH2_CLIENT_SECRET",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="github.client_secret",
            ),
            AppSecretInjection(
                env_var="CERT_RA_APP_MICROSOFT_OAUTH2_CLIENT_ID",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="microsoft.client_id",
            ),
            AppSecretInjection(
                env_var="CERT_RA_APP_MICROSOFT_OAUTH2_CLIENT_SECRET",
                secret_arn=app_props.secrets.oauth_providers.secret_arn,
                field="microsoft.client_secret",
            ),
            # RPC endpoints — RPCSettings (env_prefix="cert_ra_rpc_")
            AppSecretInjection(
                env_var="CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="ethereum_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_2",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="ethereum_private_rpc_2",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_ARBITRUM_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="arbitrum_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_BASE_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="base_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_POLYGON_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="polygon_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_SOLANA_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="solana_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_AVALANCHE_C_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="avalanche_c_private_rpc_1",
            ),
            AppSecretInjection(
                env_var="CERT_RA_RPC_OPTIMISM_PRIVATE_RPC_1",
                secret_arn=app_props.secrets.rpc_providers.secret_arn,
                field="optimism_private_rpc_1",
            ),
            # EmailSettings (env_prefix="cert_ra_email_")
            AppSecretInjection(
                env_var="CERT_RA_EMAIL_RESEND_API_KEY",
                secret_arn=app_props.secrets.email_api_key.secret_arn,
            ),
            # External SDKs that read their own env var names directly
            AppSecretInjection(
                env_var="SENTRY_DSN",
                secret_arn=app_props.secrets.sentry_dsn.secret_arn,
            ),
            AppSecretInjection(
                env_var="ANTHROPIC_API_KEY",
                secret_arn=app_props.secrets.anthropic_api_key.secret_arn,
            ),
            AppSecretInjection(
                env_var="OPENAI_API_KEY",
                secret_arn=app_props.secrets.openai_api_key.secret_arn,
            ),
            AppSecretInjection(
                env_var="THE_GRAPH_API_KEY",
                secret_arn=app_props.secrets.the_graph_api_key.secret_arn,
            ),
            # DuneSettings (env_prefix="cert_ra_dune_")
            AppSecretInjection(
                env_var="CERT_RA_DUNE_API_KEY",
                secret_arn=app_props.secrets.dune_api_key.secret_arn,
            ),
            # SuperuserSettings (env_prefix="cert_ra_superuser_")
            AppSecretInjection(
                env_var="CERT_RA_SUPERUSER_EMAIL",
                secret_arn=app_props.secrets.superuser.secret_arn,
                field="email",
            ),
            AppSecretInjection(
                env_var="CERT_RA_SUPERUSER_PASSWORD",
                secret_arn=app_props.secrets.superuser.secret_arn,
                field="password",
            ),
            # App-side Temporal mTLS triplet — needed once any route in
            # the Litestar app calls `connect_temporal`. The frontend
            # enforces client-auth mTLS, so without these env vars a
            # gRPC dial would be refused. The secret is populated by
            # TemporalStack's InitialCertIssuance Custom Resource using
            # the same flow as the worker certs.
            AppSecretInjection(
                env_var="CERT_RA_TEMPORAL_TLS_CLIENT_CERT_CONTENT",
                secret_arn=app_props.secrets.temporal_mtls_secrets["app"].secret_arn,
                field="cert",
            ),
            AppSecretInjection(
                env_var="CERT_RA_TEMPORAL_TLS_CLIENT_KEY_CONTENT",
                secret_arn=app_props.secrets.temporal_mtls_secrets["app"].secret_arn,
                field="key",
            ),
            AppSecretInjection(
                env_var="CERT_RA_TEMPORAL_TLS_CA_CERT_CONTENT",
                secret_arn=app_props.secrets.temporal_mtls_secrets["app"].secret_arn,
                field="chain",
            ),
        ]

        self.litestar = LitestarService(
            self,
            "Litestar",
            props=LitestarServiceProps(
                service_name=f"cert-ra-app-{env_config.env}",
                vpc=app_props.network.vpc.vpc,
                private_subnets=app_props.network.vpc.private_egress_subnets,
                app_security_group=app_props.network.security_groups.app,
                alb=app_props.network.public_alb.alb,
                alb_security_group=app_props.network.security_groups.alb,
                certificate_arn=app_props.dns.active.certificate_arn,
                ecr_repo_arn=app_props.identity.ecr.repository_arn,
                ecr_repo_name=app_props.identity.ecr.repository.repository_name,
                ecr_cmk_arn=app_props.identity.ecr.encryption_cmk_arn,
                image_tag=app_props.image_tag,
                # On a bootstrap deploy (initial-setup.sh's first AppStack
                # create), no real image exists yet and CodeDeploy hasn't
                # run. Setting desired_count=0 lets CFN create the service
                # definition without waiting for tasks to stabilize —
                # otherwise CodeDeploy-controlled services with
                # desired_count>0 and no pullable image time out and roll
                # back the whole stack. upgrade.sh's first run scales it
                # back up. The bootstrap state is now driven by an
                # explicit flag (not inferred from image_tag) so recovery
                # scripts that re-deploy AppStack without setting
                # CDK_APP_IMAGE_TAG don't accidentally reset prod to 0/0.
                desired_count=0 if app_props.bootstrap else DEFAULT_DESIRED_COUNT,
                secrets_cmk_arn=app_props.secrets.secrets_cmk.key.key_arn,
                secret_injections=secret_injections,
                rds_master_secret_arn=app_props.data.postgres.master_secret_arn,
                rds_master_secret_cmk_arn=app_props.data.rds_cmk.key.key_arn,
                rds_endpoint=app_props.data.postgres.endpoint_address,
                rds_port=app_props.data.postgres.endpoint_port,
                logs_cmk_arn=app_props.observability.logs_cmk.key.key_arn,
                app_url=f"https://{env_config.active_domain}",
                temporal_frontend_endpoint=app_props.temporal.cluster.frontend_endpoint,
                # Matches the SNI used by WorkersStack + MaintenanceStack;
                # set once in TemporalStack's `mtls_dns_suffix` default.
                temporal_tls_server_name="temporal-frontend.cert-ra.local",
                # Assets bucket — wires the app's StorageSettings to the
                # `cert-ra-assets-{env}` S3 bucket owned by DataStack.
                assets_bucket_name=app_props.data.assets_bucket.bucket_name,
                assets_bucket_arn=app_props.data.assets_bucket.bucket_arn,
                assets_s3_cmk_arn=app_props.data.s3_cmk.key.key_arn,
                aws_region=env_config.region,
                operator_team_name="Certora",
                operator_team_domain="certora.com",
                operator_team_enforced_provider="google",
                extra_env=app_props.extra_env,
            ),
        )

        # Per-env CodeDeploy config: linear in staging for fast
        # iteration; canary in prod to bake the canary before the cut.
        deployment_config = (
            codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES
            if env_config.env == "prod"
            else codedeploy.EcsDeploymentConfig.LINEAR_10_PERCENT_EVERY_1_MINUTES
        )
        # The test listener URL is internal — the hook Lambda lives in
        # the VPC and reaches the ALB via DNS. We point at the
        # production hostname on :8443 (same ACM cert covers it). In
        # staging we also accept the IP-only path so the hook can
        # reach the listener even when DNS hasn't propagated yet.
        test_listener_url = f"https://{app_props.dns.active.hosted_zone_name}:8443"

        self.blue_green = BlueGreenDeployment(
            self,
            "BlueGreen",
            props=BlueGreenDeploymentProps(
                application_name=f"cert-ra-app-{env_config.env}",
                deployment_group_name=f"cert-ra-app-{env_config.env}-dg",
                service=self.litestar.service,
                cluster=self.litestar.cluster,
                blue_target_group=self.litestar.blue_target_group,
                green_target_group=self.litestar.green_target_group,
                production_listener=self.litestar.production_listener,
                test_listener=self.litestar.test_listener,
                test_listener_url=test_listener_url,
                ecr_repo_arn=app_props.identity.ecr.repository_arn,
                ecr_repo_name=app_props.identity.ecr.repository.repository_name,
                cosign_pubkey_param_arn=(
                    app_props.identity.image_signing.pubkey_param_arn
                ),
                vpc=app_props.network.vpc.vpc,
                private_subnets=app_props.network.vpc.private_egress_subnets,
                alb_security_group=app_props.network.security_groups.alb,
                deployment_config=deployment_config,
                skip_tls_verify_in_smoke_test=(env_config.env != "prod"),
            ),
        )

        # Route53 alias: env apex domain → public ALB. Uses an
        # AliasTarget (not a CNAME) so we can put it on the apex and
        # so DNS resolution doesn't add an extra hop. The hosted zone
        # is imported via the cross-stack ref; the records themselves
        # live in AppStack so DnsStack stays a pure foundation.
        alb_alias_target = route53.RecordTarget.from_alias(
            route53_targets.LoadBalancerTarget(  # pyright: ignore[reportArgumentType]
                app_props.network.public_alb.alb
            )
        )
        self.alb_alias_record = route53.ARecord(
            self,
            "AlbAliasRecord",
            zone=app_props.dns.active.hosted_zone,
            record_name=app_props.dns.active.hosted_zone_name,
            target=alb_alias_target,
        )
        # Mirror the apex on `www.` so users typing the www prefix land
        # on the same ALB. ACM's wildcard SAN already covers this name.
        self.www_alias_record = route53.ARecord(
            self,
            "WwwAliasRecord",
            zone=app_props.dns.active.hosted_zone,
            record_name=f"www.{app_props.dns.active.hosted_zone_name}",
            target=alb_alias_target,
        )

        cdk.CfnOutput(
            self,
            "ServiceName",
            value=self.litestar.service.service_name,
            export_name=f"{self.stack_name}-ServiceName",
        )
        cdk.CfnOutput(
            self,
            "AppTaskDefinitionArn",
            value=self.litestar.task_definition.task_definition_arn,
            export_name=f"{self.stack_name}-AppTaskDefinitionArn",
            description=(
                "Task definition ARN for the latest registered revision. "
                "upgrade.sh consumes this when building the CodeDeploy AppSpec."
            ),
        )
        cdk.CfnOutput(
            self,
            "ClusterArn",
            value=self.litestar.cluster.cluster_arn,
            export_name=f"{self.stack_name}-ClusterArn",
        )
        cdk.CfnOutput(
            self,
            "BlueTargetGroupArn",
            value=self.litestar.blue_target_group.target_group_arn,
            export_name=f"{self.stack_name}-BlueTargetGroupArn",
        )
        cdk.CfnOutput(
            self,
            "GreenTargetGroupArn",
            value=self.litestar.green_target_group.target_group_arn,
            export_name=f"{self.stack_name}-GreenTargetGroupArn",
        )
        cdk.CfnOutput(
            self,
            "ProductionListenerArn",
            value=self.litestar.production_listener.listener_arn,
            export_name=f"{self.stack_name}-ProductionListenerArn",
        )
        cdk.CfnOutput(
            self,
            "TestListenerArn",
            value=self.litestar.test_listener.listener_arn,
            export_name=f"{self.stack_name}-TestListenerArn",
        )
        cdk.CfnOutput(
            self,
            "CodeDeployApplicationName",
            value=self.blue_green.application_name,
            export_name=f"{self.stack_name}-CodeDeployApplicationName",
        )
        cdk.CfnOutput(
            self,
            "CodeDeployDeploymentGroupName",
            value=self.blue_green.deployment_group_name,
            export_name=f"{self.stack_name}-CodeDeployDeploymentGroupName",
        )
