# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

# Defaults sized for a Temporal worker. Workers are CPU-bound on
# activity execution (RPC fetches, computation); 0.5 vCPU + 1 GiB is
# enough headroom for the metrics + alerts queues at current volumes.
DEFAULT_CPU = 512
DEFAULT_MEMORY_MIB = 1024
DEFAULT_DESIRED_COUNT = 2


def _empty_str_dict() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class WorkerSecretInjection:
    """Maps an env var name to a Secrets Manager secret ARN, optionally
    with a JSON field extraction.

    Same shape as `AppSecretInjection` in LitestarService — the
    execution role gets `GetSecretValue` per ARN automatically via
    the ECS L2; the encrypting CMK gets a separate Decrypt grant via
    `secrets_cmk_arn`.
    """

    env_var: str
    secret_arn: str
    field: str | None = None


@dataclass(frozen=True, slots=True)
class WorkerServiceProps:
    """Props for `WorkerService`.

    One instance per Temporal task queue (metrics / alerts). The
    construct is generic — task queue + entrypoint command + the
    per-worker mTLS secret are passed in by the stack.
    """

    service_name: str
    """e.g. `cert-ra-worker-metrics-staging`. Fargate service name."""

    cluster: ecs.ICluster
    """Shared ECS cluster from WorkersStack. Reusing one cluster
    across both workers keeps the Container Insights bill / control
    plane footprint flat."""

    vpc: ec2.IVpc
    private_subnets: list[ec2.ISubnet]

    worker_security_group: ec2.ISecurityGroup
    """`cert-ra-worker-sg` from NetworkStack. No ingress; outbound to
    Temporal frontend, RDS, RPC providers, AWS endpoints."""

    ecr_repo_arn: str
    ecr_repo_name: str
    ecr_cmk_arn: str
    image_tag: str
    """Image tag to deploy. Workers ship from the same `cert-ra`
    ECR repo as the Litestar app — same image, different entrypoint.
    See § Container image baselines (B4): one image, multiple
    entrypoints."""

    command: list[str]
    """Container command override, e.g. `["certora-risk-metrics-worker"]`
    for the metrics worker. This selects the entrypoint inside the
    same cert-ra image."""

    rds_master_secret_arn: str
    rds_master_secret_cmk_arn: str
    rds_endpoint: str
    rds_port: str

    secrets_cmk_arn: str
    """`cert-ra-secrets-cmk` from SecretsStack. KMS Decrypt grant for
    the execution role covers all `secret_injections` AND the per-
    worker mTLS cert in one grant."""

    secret_injections: list[WorkerSecretInjection]
    """Per-worker runtime secrets (RPC providers, Sentry, etc.).
    Stack-level concern: WorkersStack passes the relevant subset
    of SecretsStack's entries."""

    worker_mtls_secret_arn: str
    """ARN of this worker's SeededSecret from SecretsStack (one of
    `temporal_mtls_secrets["worker-metrics"]` / `["worker-alerts"]`).
    The construct mounts cert/key/chain as separate ECS Secrets
    via `field=` extraction against the JSON payload populated by
    InitialCertIssuance / CertRenewal."""

    temporal_frontend_endpoint: str
    """Temporal frontend NLB DNS name from TemporalStack. Workers
    connect here over mTLS (port 7233)."""

    temporal_tls_server_name: str
    """SNI / cert-CN the worker validates against the Temporal
    frontend cert. Always `temporal-frontend.cert-ra.local` for
    cert-ra deploys, but parameterised so future split clusters
    can override."""

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` from ObservabilityStack."""

    container_port: int | None = None
    """Workers typically don't expose a port. Set to a metrics-export
    port if a sidecar scraper (ADOT) is added later."""

    cpu: int = DEFAULT_CPU
    memory_mib: int = DEFAULT_MEMORY_MIB
    desired_count: int = DEFAULT_DESIRED_COUNT
    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_MONTH

    extra_env: dict[str, str] = field(default_factory=_empty_str_dict)
    """Plain (non-secret) env vars layered on top of the construct's
    defaults. Workers typically set `LOG_LEVEL`, `OTEL_*`, etc. here."""


class WorkerService(Construct):
    """Fargate service running a cert-ra Temporal worker.

    What this provisions:

    - Fargate task definition pulling from the `cert-ra` ECR repo at
      the configured `image_tag`, with the per-worker entrypoint
      command (`certora-risk-metrics-worker` or
      `certora-risk-alerts-worker`).
    - ECS service on the **shared** WorkersStack cluster with the
      **rolling deployment controller** + deployment circuit breaker
      enabled (catches failed deploys within ~3 min instead of
      letting them stagger for hours). No CodeDeploy: workers have no
      public listener to traffic-shift, so the rolling controller is
      the right primitive (design § Blue/green for the public app —
      "WorkersStack and TemporalStack keep ECS rolling deployments").
    - Per-worker mTLS cert injection: cert/key/chain content read
      from the per-worker SeededSecret's JSON fields and injected as
      separate ECS Secrets (`TEMPORAL_TLS_CLIENT_CERT_CONTENT` /
      `_KEY_CONTENT` / `_CA_CERT_CONTENT`). The cert-ra worker code
      reads these env vars at startup and constructs a Temporal SDK
      `TLSConfig`. No file mount + entrypoint shim needed because the
      Temporal Python SDK accepts in-memory PEM bytes directly.
    - Per-worker CloudWatch log group encrypted with `cert-ra-logs-cmk`.
    - Worker container env: `TASK_QUEUE` (from the command's task
      queue convention), `TEMPORAL_ADDRESS`, `TEMPORAL_TLS_SERVER_NAME`,
      DB connection vars, plus any `extra_env` and `secret_injections`
      passed by the stack.
    - No port mappings, no target group registration, no NLB / ALB
      attachment — the worker only connects OUT.

    What it does NOT do:
    - Autoscaling on Temporal backlog metrics. Initial deploy uses
      `desired_count=2` (one per AZ) with no autoscaling. Backlog-
      driven autoscaling lands in a follow-up once we have a stable
      baseline for the steady-state backlog distribution.
    """

    task_definition: ecs.FargateTaskDefinition
    container: ecs.ContainerDefinition
    service: ecs.FargateService
    log_group: logs.LogGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: WorkerServiceProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        logs_cmk = kms.Key.from_key_arn(self, "LogsCmk", props.logs_cmk_arn)
        self.log_group = logs.LogGroup(
            self,
            "LogGroup",
            log_group_name=f"/ecs/{props.service_name}",
            retention=props.log_retention,
            encryption_key=logs_cmk,
            # See LitestarService for the DESTROY rationale: prevents
            # `already exists` errors when a CREATE rolls back.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        ecr_repo = ecr.Repository.from_repository_attributes(
            self,
            "EcrRepo",
            repository_arn=props.ecr_repo_arn,
            repository_name=props.ecr_repo_name,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=props.cpu,
            memory_limit_mib=props.memory_mib,
            family=props.service_name,
            # Image is built for linux/arm64 (build.yml --platform linux/arm64).
            # Without this Fargate defaults to X86_64 and fails to pull.
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "RdsSecret", props.rds_master_secret_arn
        )
        rds_cmk = kms.Key.from_key_arn(self, "RdsCmk", props.rds_master_secret_cmk_arn)
        mtls_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "WorkerMtlsSecret", props.worker_mtls_secret_arn
        )

        env: dict[str, str] = {
            "DATABASE_HOST": props.rds_endpoint,
            "DATABASE_PORT": props.rds_port,
            "TEMPORAL_ADDRESS": props.temporal_frontend_endpoint,
            "TEMPORAL_TLS_SERVER_NAME": props.temporal_tls_server_name,
        }
        env.update(props.extra_env)

        secrets: dict[str, ecs.Secret] = {
            "DATABASE_USER": ecs.Secret.from_secrets_manager(
                rds_secret, field="username"
            ),
            "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                rds_secret, field="password"
            ),
            # mTLS triplet — populated by InitialCertIssuance and refreshed
            # by CertRenewal. The Python worker reads these env vars and
            # builds a Temporal SDK `TLSConfig(client_cert=..., client_private_key=...,
            # server_root_ca_cert=...)`; no file mount needed because the
            # SDK accepts PEM bytes directly.
            "TEMPORAL_TLS_CLIENT_CERT_CONTENT": ecs.Secret.from_secrets_manager(
                mtls_secret, field="cert"
            ),
            "TEMPORAL_TLS_CLIENT_KEY_CONTENT": ecs.Secret.from_secrets_manager(
                mtls_secret, field="key"
            ),
            "TEMPORAL_TLS_CA_CERT_CONTENT": ecs.Secret.from_secrets_manager(
                mtls_secret, field="chain"
            ),
        }
        for injection in props.secret_injections:
            injected_secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                f"InjectedSecret{injection.env_var}",
                injection.secret_arn,
            )
            secrets[injection.env_var] = ecs.Secret.from_secrets_manager(
                injected_secret, field=injection.field
            )

        port_mappings = (
            [
                ecs.PortMapping(
                    container_port=props.container_port, protocol=ecs.Protocol.TCP
                )
            ]
            if props.container_port is not None
            else []
        )

        self.container = self.task_definition.add_container(
            "Worker",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repo, props.image_tag),
            essential=True,
            command=props.command,
            environment=env,
            secrets=secrets,
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="worker",
                log_group=self.log_group,
            ),
            port_mappings=port_mappings,
        )

        # Imported CMKs don't auto-grant on the execution role; do it
        # explicitly. obtain_execution_role materialises the role
        # before grants attach.
        execution_role = self.task_definition.obtain_execution_role()
        rds_cmk.grant_decrypt(execution_role)
        secrets_cmk = kms.Key.from_key_arn(self, "SecretsCmk", props.secrets_cmk_arn)
        secrets_cmk.grant_decrypt(execution_role)
        ecr_cmk = kms.Key.from_key_arn(self, "EcrCmk", props.ecr_cmk_arn)
        ecr_cmk.grant_decrypt(execution_role)

        # ECS service: rolling deployment (default controller) with
        # deployment circuit breaker. Without the circuit breaker, a
        # failed task starts can spin for ~3 hours before ECS marks
        # the deploy as failed; the breaker drops that to 10 attempts.
        self.service = ecs.FargateService(
            self,
            "Service",
            cluster=props.cluster,
            task_definition=self.task_definition,
            service_name=props.service_name,
            desired_count=props.desired_count,
            security_groups=[props.worker_security_group],
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=50,
            max_healthy_percent=200,
            enable_execute_command=True,
            assign_public_ip=False,
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "ECS L2 TaskDefinition auto-creates the execution "
                        "role with an inline policy for secrets + KMS + CW "
                        "Logs + ECR. We don't author this policy directly."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "CW Logs writes use wildcards on log-stream name; "
                        "ECR pulls use wildcards on layer digests. Neither "
                        "is predictable at deploy time."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "Non-secret env vars (DATABASE_HOST, TEMPORAL_ADDRESS, "
                        "TEMPORAL_TLS_SERVER_NAME) are connection metadata. "
                        "All credentials + cert material are injected via "
                        "ECS Secrets, not plaintext env vars."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS4",
                    "reason": (
                        "Container Insights v2 is enabled on the shared "
                        "WorkersStack cluster (set when the cluster is "
                        "created in the stack), satisfying this control."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def task_role(self) -> iam.IRole:
        """The container's runtime IAM role. Consumers extend it for
        per-worker S3 / DynamoDB / etc. grants outside the
        `secret_injections` list."""
        return self.task_definition.task_role
