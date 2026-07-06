# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from cdk_nag import NagSuppressions
from constructs import Construct

# Docker asset dir for the custom maintenance image. Multi-stage
# build: stage 1 downloads the temporal CLI tarball (curl + tar
# live there); stage 2 is the runtime image (no curl/wget per H2).
# Also includes `temporal-wrapper.sh` which the Dockerfile COPYs
# in as /usr/local/bin/temporal.
_DOCKER_ASSET_DIR = Path(__file__).parent / "_docker" / "maint"

# Smallest viable size for an idle Fargate task (~$8/mo idle cost per
# env). Container does no real work — it just needs to be reachable
# for ECS Exec.
DEFAULT_CPU = 256
DEFAULT_MEMORY_MIB = 512

# `sleep infinity` keeps the container alive forever. Operators
# connect via `aws ecs execute-command`; the long-lived sleep keeps
# the task in RUNNING state without consuming CPU.
MAINT_COMMAND = ["sleep", "infinity"]

# Health check: trivial exit-0 script. The task does no work; this
# satisfies the ECS health check loop without making it look like
# the container is doing anything.
MAINT_HEALTH_CHECK_CMD = ["CMD-SHELL", "exit 0"]


@dataclass(frozen=True, slots=True)
class MaintenanceContainerProps:
    """Props for `MaintenanceContainer`. See § MaintenanceContainer
    construct in the design spec for the security model (A1, A4, H2)."""

    service_name: str
    """e.g. `cert-ra-maint-staging`."""

    env_name: str
    """`staging` or `prod` — used to scope the maint task role's
    Secrets Manager read to `/cert-ra/${env_name}/*` and to build
    the A4 Deny ARNs for worker mTLS secrets."""

    cluster: ecs.ICluster
    """Dedicated `cert-ra-maint-${env}` cluster from MaintenanceStack
    (A1 isolation — operators can't Exec into app/worker tasks because
    they live in a different cluster)."""

    vpc: ec2.IVpc
    private_subnets: list[ec2.ISubnet]

    maint_security_group: ec2.ISecurityGroup
    """`cert-ra-maint-sg` from NetworkStack. allow_all_outbound=False;
    egress to RDS:5432 + Temporal:7233 is already wired in NetworkStack;
    we add egress to the VPC endpoint SGs here."""

    vpc_endpoint_security_groups: list[ec2.ISecurityGroup]
    """Security groups of the interface VPC endpoints (Secrets Manager,
    KMS, ECR, CW Logs, STS, SSM Messages). The maint container can't
    reach the public internet (no NAT path; H2), so AWS SDK calls go
    through these endpoints. We add a single 443 egress rule per
    endpoint SG so SDK calls succeed."""

    # No ECR repo / image_tag here — the maint image is built from
    # the multi-stage Dockerfile next to this module via
    # `ContainerImage.from_asset`. CDK uploads the asset to the
    # bootstrap ECR repo. The image is independent of the cert-ra app
    # image; schema-mutating commands belong on MigrationsStack's
    # cert-ra-migrate task definition, not here.

    rds_master_secret_arn: str
    rds_master_secret_cmk_arn: str
    rds_endpoint: str
    rds_port: str

    secrets_cmk_arn: str
    """`cert-ra-secrets-cmk` from SecretsStack. KMS Decrypt grant for
    the execution role covers the mTLS secret + RDS secret."""

    maint_mtls_secret_arn: str
    """`temporal_mtls_secrets["maint"]` from SecretsStack. The
    container reads its cert/key/chain to talk to Temporal via the
    `temporal` CLI wrapper (see § MaintenanceContainer integration in
    the design spec)."""

    temporal_frontend_endpoint: str
    """Temporal NLB DNS name. Wrapper around `temporal` CLI passes
    this as `--address`."""

    temporal_tls_server_name: str
    """SNI for cert validation. `temporal-frontend.cert-ra.local`."""

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` from ObservabilityStack."""

    cpu: int = DEFAULT_CPU
    memory_mib: int = DEFAULT_MEMORY_MIB
    log_retention: logs.RetentionDays = logs.RetentionDays.THREE_MONTHS
    """ECS Exec session logs land in this group; 90 days matches the
    audit retention noted in the design spec § Maintenance container."""


class MaintenanceContainer(Construct):
    """Always-on Fargate task for operator-driven inspection / repair.

    Per the design spec § MaintenanceContainer:
    - **A1**: lives on its own ECS cluster (passed in from
      MaintenanceStack) so Upgrader's `ecs:ExecuteCommand` IAM scope
      only opens this one cluster's tasks — not app / worker tasks.
    - **A4**: task role grants Secrets Manager read on
      `/cert-ra/${env}/*` but explicitly denies the worker + internal-
      worker mTLS secrets. Maint can read its OWN cert + the app /
      OAuth / RPC secrets — what an operator legitimately needs —
      but CANNOT impersonate a worker at Temporal.
    - **H2**: maint SG has `allow_all_outbound=False` (NetworkStack);
      egress is opted in to RDS, Temporal, and the VPC endpoints we
      configure here. No NAT path; an attacker inside the container
      cannot reach the public internet directly.
    - `enable_execute_command=True` on the service so `aws ecs
      execute-command` works without per-task opt-in.
    - Container `command=["sleep", "infinity"]` — the task does no
      real work; it just needs to be reachable for Exec.

    What it does NOT yet include (deferred to follow-up PRs):
    - Custom image layer with `psql`, `temporal` CLI, and the
      `temporal` wrapper script (design spec § MaintenanceContainer
      integration). Until this lands operators run inside the
      stock cert-ra image and invoke the Temporal SDK from Python.
    - H2-A VPC endpoint policies (already present in NetworkStack
      via `_account_scope_endpoint_statement`).
    - H2-B Exec session log alarming + H2-C VPC Flow Logs REJECT
      alarming — they land in ObservabilityStack alongside the
      other monitoring infrastructure.
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
        props: MaintenanceContainerProps,
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
            # `already exists` errors when a CREATE rolls back. We hit
            # exactly this on `/ecs/cert-ra-maint-prod` during the
            # 2026-06-05 prod recovery.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # Multi-stage local Docker build → CDK bootstrap ECR repo.
        # The asset hash is the SHA256 of the build context's
        # contents (Dockerfile + temporal-wrapper.sh), so a wrapper
        # script change invalidates the cache and forces a re-push.
        maint_image = ecs.ContainerImage.from_asset(
            str(_DOCKER_ASSET_DIR),
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=props.cpu,
            memory_limit_mib=props.memory_mib,
            family=props.service_name,
        )

        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "RdsSecret", props.rds_master_secret_arn
        )
        rds_cmk = kms.Key.from_key_arn(self, "RdsCmk", props.rds_master_secret_cmk_arn)
        maint_mtls = secretsmanager.Secret.from_secret_complete_arn(
            self, "MaintMtlsSecret", props.maint_mtls_secret_arn
        )

        self.container = self.task_definition.add_container(
            "Maint",
            image=maint_image,
            essential=True,
            command=MAINT_COMMAND,
            environment={
                "DATABASE_HOST": props.rds_endpoint,
                "DATABASE_PORT": props.rds_port,
                "TEMPORAL_ADDRESS": props.temporal_frontend_endpoint,
                "TEMPORAL_TLS_SERVER_NAME": props.temporal_tls_server_name,
                "CERT_RA_ENV": props.env_name,
            },
            secrets={
                "DATABASE_USER": ecs.Secret.from_secrets_manager(
                    rds_secret, field="username"
                ),
                "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                    rds_secret, field="password"
                ),
                "TEMPORAL_TLS_CLIENT_CERT_CONTENT": ecs.Secret.from_secrets_manager(
                    maint_mtls, field="cert"
                ),
                "TEMPORAL_TLS_CLIENT_KEY_CONTENT": ecs.Secret.from_secrets_manager(
                    maint_mtls, field="key"
                ),
                "TEMPORAL_TLS_CA_CERT_CONTENT": ecs.Secret.from_secrets_manager(
                    maint_mtls, field="chain"
                ),
            },
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="maint",
                log_group=self.log_group,
            ),
            health_check=ecs.HealthCheck(
                command=MAINT_HEALTH_CHECK_CMD,
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(10),
            ),
        )

        execution_role = self.task_definition.obtain_execution_role()
        rds_cmk.grant_decrypt(execution_role)
        secrets_cmk = kms.Key.from_key_arn(self, "SecretsCmk", props.secrets_cmk_arn)
        secrets_cmk.grant_decrypt(execution_role)
        # No ECR CMK grant: CDK's bootstrap ECR repo (where
        # ContainerImage.from_asset uploads to) auto-grants the
        # execution role read access through the asset's own
        # grant_pull call.

        # --- Task role: A4-scoped Secrets Manager + ECS Exec channels ---
        task_role = self.task_definition.task_role
        account = cdk.Aws.ACCOUNT_ID
        region = cdk.Aws.REGION

        # Wildcard read across /cert-ra/${env}/* …
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="ReadEnvSecrets",
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    (
                        f"arn:aws:secretsmanager:{region}:{account}:"
                        f"secret:/cert-ra/{props.env_name}/*"
                    ),
                ],
            )
        )
        # … with explicit Deny on every OTHER service's mTLS material
        # (A4). A compromised maint container must NOT be able to
        # impersonate any worker, the internal-worker role, or the app
        # at Temporal. Maint can still read its own `maint` cert via
        # the wildcard above.
        peer_mtls_arn_patterns = [
            (
                f"arn:aws:secretsmanager:{region}:{account}:"
                f"secret:/cert-ra/{props.env_name}/temporal/mtls/worker-*"
            ),
            (
                f"arn:aws:secretsmanager:{region}:{account}:"
                f"secret:/cert-ra/{props.env_name}/temporal/mtls/internal-worker*"
            ),
            (
                f"arn:aws:secretsmanager:{region}:{account}:"
                f"secret:/cert-ra/{props.env_name}/temporal/mtls/app*"
            ),
        ]
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="DenyReadPeerMtlsSecrets",
                effect=iam.Effect.DENY,
                actions=["secretsmanager:GetSecretValue"],
                resources=peer_mtls_arn_patterns,
            )
        )

        # ECS Exec channels: ssmmessages:* lets the SSM agent inside
        # the container open the control + data channels back to
        # Session Manager. Without these the `aws ecs execute-command`
        # invocation fails to attach.
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="EcsExecChannels",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                resources=["*"],
            )
        )
        # CW Logs for ECS Exec session logging — the session
        # transcript writes to our log group.
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="WriteExecSessionLogs",
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:DescribeLogGroups",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[
                    self.log_group.log_group_arn,
                    f"{self.log_group.log_group_arn}:*",
                ],
            )
        )

        # --- Maint SG egress to VPC endpoints on 443 ---
        # The maint SG has allow_all_outbound=False (NetworkStack);
        # without these, AWS SDK calls from the container fail
        # because the SG drops the outbound packets even though the
        # VPC routing would send them through the endpoint ENIs.
        for idx, endpoint_sg in enumerate(props.vpc_endpoint_security_groups):
            props.maint_security_group.add_egress_rule(
                peer=endpoint_sg,
                connection=ec2.Port.tcp(443),
                description=f"Maint to VPC endpoint #{idx}",
            )

        # --- Service: always-on, desired=1, ECS Exec enabled ---
        self.service = ecs.FargateService(
            self,
            "Service",
            cluster=props.cluster,
            task_definition=self.task_definition,
            service_name=props.service_name,
            desired_count=1,
            security_groups=[props.maint_security_group],
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            enable_execute_command=True,
            assign_public_ip=False,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=0,
            max_healthy_percent=200,
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "ECS L2 TaskDefinition auto-creates the execution role "
                        "with an inline policy for secrets + KMS + CW Logs + ECR. "
                        "We don't author this policy directly."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "Task role's Secrets Manager read uses a wildcard "
                        "ARN (`/cert-ra/${env}/*`) by design — operators need "
                        "broad read; worker mTLS material is carved out via "
                        "explicit Deny. CW Logs writes use wildcards on "
                        "log-stream name; ECR pulls use wildcards on layer "
                        "digests."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "DATABASE_HOST + TEMPORAL_ADDRESS + CERT_RA_ENV are "
                        "non-secret connection metadata; credentials + cert "
                        "material are injected via ECS Secrets."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS4",
                    "reason": (
                        "Container Insights v2 is enabled on the dedicated "
                        "MaintenanceStack cluster."
                    ),
                },
            ],
            apply_to_children=True,
        )

    @property
    def task_role_arn(self) -> str:
        return self.task_definition.task_role.role_arn
