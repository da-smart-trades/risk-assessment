# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_ecs as ecs
from constructs import Construct

from cert_ra_infra.constructs.workers.worker_service import (
    WorkerSecretInjection,
    WorkerService,
    WorkerServiceProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack
from cert_ra_infra.stacks.identity import IdentityStack
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import ObservabilityStack
from cert_ra_infra.stacks.secrets import SecretsStack
from cert_ra_infra.stacks.temporal import TemporalStack

# Default image tag — same convention as AppStack. CI pins to
# `sha-<git_sha>` per the ECR repo's IMMUTABLE policy.
DEFAULT_IMAGE_TAG = "latest"

# Worker task queue → entrypoint command. Driven by the two scripts
# defined in pyproject.toml's [project.scripts]:
#   certora-risk-metrics-worker = "cert_ra.metrics.worker:main"
#   certora-risk-alerts-worker  = "cert_ra.alerts.worker:main"
# The task queue names match what the worker code registers against
# (`src/cert_ra/{metrics,alerts}/worker.py::TASK_QUEUE`).
_WORKER_ENTRYPOINTS: dict[str, list[str]] = {
    "metrics": ["certora-risk-metrics-worker"],
    "alerts": ["certora-risk-alerts-worker"],
}

# SNI / cert-CN the workers validate against the Temporal frontend
# cert. Must match the `TemporalServiceCertConfig.common_name` for
# the frontend service in TemporalStack.
_TEMPORAL_FRONTEND_SNI = "temporal-frontend.cert-ra.local"

# Canton Global Synchronizer Scan API roots for the metrics collector
# (CantonSettings.scan_urls, read as a JSON list). Not secrets, so they live
# here as plain task env rather than in SecretsStack — mirroring the public RPC
# fallbacks baked into RPCSettings. Using the public cantonnodes proxy (free,
# rate-limited, no allow-listing) which serves real MainNet data at /v0/*. The
# raw SV scans (scan.sv-1/sv-2.global.canton.network.digitalasset.com) are
# IP-allow-listed; swap them in once the workers' NAT egress IP is allow-listed
# by an SV sponsor, and CantonScanClient will query + reconcile across them.
_CANTON_SCAN_URLS = '["https://api.cantonnodes.com"]'


def _empty_str_dict() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class WorkersStackProps:
    """Stack-level inputs for WorkersStack."""

    network: NetworkStack
    data: DataStack
    secrets: SecretsStack
    observability: ObservabilityStack
    identity: IdentityStack
    temporal: TemporalStack

    image_tag: str = DEFAULT_IMAGE_TAG
    """Image tag to deploy. Defaults to `latest`; CI overrides with
    `sha-<git_sha>`."""

    extra_env: dict[str, str] = field(default_factory=_empty_str_dict)
    """Plain (non-secret) env vars layered on top of WorkerService's
    defaults. Applied to BOTH workers — use this for project-wide
    settings like `LOG_LEVEL`."""


class WorkersStack(Stack):
    """ECS workers for the Temporal `metrics` and `alerts` task queues.

    Two Fargate services (`cert-ra-worker-metrics-${env}` +
    `cert-ra-worker-alerts-${env}`) share a single ECS cluster. Each
    runs the same `cert-ra` image with a different entrypoint command
    selecting the right `[project.scripts]` console script.

    Rolling deploy with the deployment circuit breaker (not blue/green
    — workers have no public listener to traffic-shift; design § Blue/
    green explicitly carves WorkersStack out of CodeDeploy).

    Cross-stack refs (string-ARN pattern): RDS, secrets, ECR, KMS CMKs.
    The Temporal frontend endpoint comes from `TemporalStack.cluster.
    frontend_endpoint` (the internal NLB DNS name).
    """

    cluster: ecs.Cluster
    metrics_worker: WorkerService
    alerts_worker: WorkerService

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        workers_props: WorkersStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        # Shared cluster — both workers run here. Container Insights
        # v2 on the cluster covers both services' suppressions for
        # AwsSolutions-ECS4.
        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"cert-ra-workers-{env_config.env}",
            vpc=workers_props.network.vpc.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # Shared runtime secrets across both workers. Session / OAuth /
        # email secrets aren't needed worker-side. Each entry uses the
        # exact env var name the app/SDK reads — no JSON blob expansion.
        shared_secret_injections = [
            # RPCSettings (env_prefix="cert_ra_rpc_")
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="ethereum_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_ETHEREUM_PRIVATE_RPC_2",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="ethereum_private_rpc_2",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_ARBITRUM_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="arbitrum_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_BASE_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="base_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_POLYGON_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="polygon_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_SOLANA_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="solana_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_AVALANCHE_C_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="avalanche_c_private_rpc_1",
            ),
            WorkerSecretInjection(
                env_var="CERT_RA_RPC_OPTIMISM_PRIVATE_RPC_1",
                secret_arn=workers_props.secrets.rpc_providers.secret_arn,
                field="optimism_private_rpc_1",
            ),
            # External SDKs that read their own env var names directly
            WorkerSecretInjection(
                env_var="SENTRY_DSN",
                secret_arn=workers_props.secrets.sentry_dsn.secret_arn,
            ),
            WorkerSecretInjection(
                env_var="ANTHROPIC_API_KEY",
                secret_arn=workers_props.secrets.anthropic_api_key.secret_arn,
            ),
            WorkerSecretInjection(
                env_var="OPENAI_API_KEY",
                secret_arn=workers_props.secrets.openai_api_key.secret_arn,
            ),
            WorkerSecretInjection(
                env_var="THE_GRAPH_API_KEY",
                secret_arn=workers_props.secrets.the_graph_api_key.secret_arn,
            ),
            # DuneSettings (env_prefix="cert_ra_dune_")
            WorkerSecretInjection(
                env_var="CERT_RA_DUNE_API_KEY",
                secret_arn=workers_props.secrets.dune_api_key.secret_arn,
            ),
        ]

        self.metrics_worker = self._build_worker(
            queue="metrics", props=workers_props, secrets=shared_secret_injections
        )
        self.alerts_worker = self._build_worker(
            queue="alerts", props=workers_props, secrets=shared_secret_injections
        )

        cdk.CfnOutput(
            self,
            "ClusterArn",
            value=self.cluster.cluster_arn,
            export_name=f"{self.stack_name}-ClusterArn",
        )
        cdk.CfnOutput(
            self,
            "MetricsWorkerServiceName",
            value=self.metrics_worker.service.service_name,
            export_name=f"{self.stack_name}-MetricsWorkerServiceName",
        )
        cdk.CfnOutput(
            self,
            "AlertsWorkerServiceName",
            value=self.alerts_worker.service.service_name,
            export_name=f"{self.stack_name}-AlertsWorkerServiceName",
        )

    def _build_worker(
        self,
        *,
        queue: str,
        props: WorkersStackProps,
        secrets: list[WorkerSecretInjection],
    ) -> WorkerService:
        # Look up the corresponding SeededSecret in SecretsStack.
        # `temporal_mtls_secrets` is keyed by the same names used in
        # TemporalMtlsPki (worker-metrics / worker-alerts / ...).
        secret_key = f"worker-{queue}"
        worker_mtls_secret = props.secrets.temporal_mtls_secrets[secret_key]

        # Per-worker env: TASK_QUEUE distinguishes the worker's polling
        # behaviour even though the entrypoint command already pins it
        # — this gives operators a quick `aws ecs describe-task-definition`
        # / log-grep signal without reading the worker source.
        env = {"TASK_QUEUE": queue, **props.extra_env}
        # The alerts worker exits cleanly on startup unless this flag is
        # set (see `cert_ra.alerts.worker.run_worker`). The gate lives on
        # the worker that reads it, not on the app.
        if queue == "alerts":
            env["CERT_RA_TEMPORAL_ALERTS_ENABLED"] = "true"
        # The Canton finality/throughput/decentralization collectors run on
        # the metrics queue and need the public Scan API roots. Set only on
        # the worker that reads them (the API/app don't fetch from Scan).
        if queue == "metrics":
            env["CERT_RA_CANTON_SCAN_URLS"] = _CANTON_SCAN_URLS

        # The metrics worker fans out across many chains (Solana, Polygon,
        # Arbitrum, Optimism, Base, Unichain, Avalanche, Canton, ...) and
        # holds persistent web3 WebSocket subscriptions per chain. The
        # construct default of 1 GiB OOM-kills it under steady load; bump
        # to 4 GiB. Alerts stays at the default — it runs a single light
        # workflow type and the gate keeps it idle when disabled.
        memory_mib = 4096 if queue == "metrics" else 1024

        return WorkerService(
            self,
            f"Worker{queue.title()}",
            props=WorkerServiceProps(
                service_name=f"cert-ra-worker-{queue}-{self.env_config.env}",
                cluster=self.cluster,
                vpc=props.network.vpc.vpc,
                private_subnets=props.network.vpc.private_egress_subnets,
                worker_security_group=props.network.security_groups.worker,
                ecr_repo_arn=props.identity.ecr.repository_arn,
                ecr_repo_name=props.identity.ecr.repository.repository_name,
                ecr_cmk_arn=props.identity.ecr.encryption_cmk_arn,
                image_tag=props.image_tag,
                command=_WORKER_ENTRYPOINTS[queue],
                rds_master_secret_arn=props.data.postgres.master_secret_arn,
                rds_master_secret_cmk_arn=props.data.rds_cmk.key.key_arn,
                rds_endpoint=props.data.postgres.endpoint_address,
                rds_port=props.data.postgres.endpoint_port,
                secrets_cmk_arn=props.secrets.secrets_cmk.key.key_arn,
                secret_injections=secrets,
                worker_mtls_secret_arn=worker_mtls_secret.secret_arn,
                temporal_frontend_endpoint=(props.temporal.cluster.frontend_endpoint),
                temporal_tls_server_name=_TEMPORAL_FRONTEND_SNI,
                logs_cmk_arn=props.observability.logs_cmk.key.key_arn,
                memory_mib=memory_mib,
                extra_env=env,
            ),
        )
