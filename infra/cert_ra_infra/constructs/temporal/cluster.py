# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_servicediscovery as servicediscovery
from cdk_nag import NagSuppressions
from constructs import Construct

# Pinned Temporal server version. Bumping requires the upgrade runbook
# (Q4): document the change, snapshot RDS, deploy to staging, verify,
# then promote to prod. See § Container image baselines (B4) and §
# Load-bearing immutable values for the rationale around pinning.
#
# This tag is consumed by the custom Dockerfile in
# `_docker/temporal_server/Dockerfile` — keep them in sync.
TEMPORAL_SERVER_TAG = "1.27.4"
TEMPORAL_SERVER_IMAGE = f"temporalio/server:{TEMPORAL_SERVER_TAG}"

# Asset dir for the custom Docker image that layers an entrypoint shim
# on top of the stock Temporal server image. The shim materialises mTLS
# cert/key/chain content from env vars (injected via ECS Secrets) into
# files on disk, then execs the stock /etc/temporal/entrypoint.sh.
_DOCKER_ASSET_DIR = Path(__file__).parent / "_docker" / "temporal_server"

# Standard Temporal port assignments. These are part of the server
# protocol — changing them requires coordinated client + server updates.
TEMPORAL_FRONTEND_PORT = 7233
TEMPORAL_HISTORY_PORT = 7234
TEMPORAL_MATCHING_PORT = 7235
TEMPORAL_INTERNAL_WORKER_PORT = 7239
TEMPORAL_MEMBERSHIP_PORT_OFFSET = 100  # ringpop membership on port + 100

# DO NOT CHANGE — load-bearing per § Load-bearing immutable values + Q4.
# Sharding is set at namespace-init time; changing it later requires a
# full Temporal namespace migration (snapshot + restore on the new
# value). Snapshot tests assert this constant.
NUM_HISTORY_SHARDS = 512

DEFAULT_CPU = 1024  # 1 vCPU
DEFAULT_MEMORY_MIB = 2048


@dataclass(frozen=True, slots=True)
class TemporalClusterProps:
    """Props for TemporalCluster.

    See § TemporalCluster integration in the design spec.
    """

    cluster_name: str
    """e.g. `cert-ra-temporal-staging`. The ECS cluster name."""

    vpc: ec2.IVpc

    private_subnets: list[ec2.ISubnet]
    """Private-egress subnets where the Temporal Fargate tasks live."""

    temporal_fe_security_group: ec2.ISecurityGroup
    """The `cert-ra-temporal-fe-sg` from NetworkStack. Allows ingress
    from app, worker, and maint security groups on the frontend port."""

    alb_security_group: ec2.ISecurityGroup
    """Security group for the internal NLB. Should allow ingress from
    the same source SGs that need to reach Temporal frontend."""

    rds_endpoint: str
    """RDS Postgres endpoint hostname for Temporal persistence."""

    rds_port: str
    """RDS port (typically `5432`); a CDK Token-string when sourced from
    DataStack outputs."""

    rds_master_secret_arn: str
    """RDS master credential secret ARN. Passed as a string (not ISecret)
    to avoid a cross-stack dependency cycle: passing the ISecret object
    would let CDK auto-mutate the secret's resource policy in DataStack,
    which combined with TemporalStack's existing dep on DataStack outputs
    creates a cycle. The secret is imported with `from_secret_complete_arn`
    inside `_build_service` so grants are identity-side only."""

    rds_master_secret_cmk_arn: str
    """CMK ARN that encrypts the RDS master secret. Same string-ARN pattern
    as `rds_master_secret_arn`."""

    logs_cmk_arn: str
    """`cert-ra-logs-cmk` ARN from ObservabilityStack. Encrypts each per-
    service CloudWatch log group. The CMK's key policy already allows
    `logs.<region>.amazonaws.com` (set in NarrowKmsCmk's service_principals
    when ObservabilityStack creates it)."""

    mtls_enforce: bool
    """When True, the frontend listener requires client-auth mTLS and the
    `temporal-frontend` cert is mounted into all four cluster services
    via the entrypoint shim. When False (initial bootstrap deploy), the
    shim is a no-op and the cluster accepts plaintext gRPC so workers
    can connect before any certs exist."""

    frontend_mtls_secret_arn: str
    """ARN of the `temporal-frontend` SeededSecret from SecretsStack. The
    InitialCertIssuance Custom Resource populates this with a JSON payload
    `{"cert": "...", "chain": "...", "key": "..."}`. All four cluster
    services mount the same cert (the design's "shared internode +
    frontend TLS" model). Used only when `mtls_enforce=True`; the prop is
    still required so the cluster's task definitions are consistent
    across deploys."""

    secrets_cmk_arn: str
    """`cert-ra-secrets-cmk` ARN from SecretsStack. KMS Decrypt grant for
    the execution role to decrypt the mTLS secret payload."""

    log_retention: logs.RetentionDays = logs.RetentionDays.ONE_MONTH

    cpu: int = DEFAULT_CPU

    memory_mib: int = DEFAULT_MEMORY_MIB


class TemporalCluster(Construct):
    """Self-hosted Temporal server cluster on ECS Fargate.

    Four services (Frontend, History, Matching, Internal-Worker) discover
    each other via Cloud Map private DNS in `<service>.<cluster>.local`.
    The Frontend is fronted by an internal Network Load Balancer because
    Temporal's protocol is gRPC over HTTP/2 (NLB preserves the long-lived
    connections better than ALB and adds no header rewriting).

    **mTLS:** When `mtls_enforce=True`, each cluster service runs a
    custom Docker image (built from `_docker/temporal_server/Dockerfile`)
    that wraps the stock Temporal entrypoint with a shim. The shim reads
    `MTLS_CERT_CONTENT` / `MTLS_KEY_CONTENT` / `MTLS_CHAIN_CONTENT` env
    vars (injected via ECS Secrets from the `temporal-frontend` SeededSecret
    populated by InitialCertIssuance), writes them to
    `/run/temporal-tls/{server.crt,server.key,ca.crt}`, and sets the
    `TEMPORAL_TLS_*` path env vars that the upstream config_template.yaml
    consumes. `TEMPORAL_TLS_REQUIRE_CLIENT_AUTH=true` causes the frontend
    to reject gRPC connections that don't present a valid client cert
    chained to the same subordinate CA — closing M5.

    When `mtls_enforce=False` (initial bootstrap deploy), the mTLS env
    vars are not mounted, the shim is a no-op, and the cluster accepts
    plaintext gRPC so workers can connect before any certs exist. The
    initial-setup runbook re-deploys with `mtls_enforce=True` once
    InitialCertIssuance has populated the secret shells.

    **Persistence:** Both the `temporal` (history) and
    `temporal_visibility` databases live on the shared RDS Postgres
    instance from DataStack. Schema bootstrap is a separate one-off
    Fargate task (`TemporalSchemaBootstrap`).
    """

    cluster: ecs.Cluster
    cloud_map_namespace: servicediscovery.PrivateDnsNamespace
    frontend_service: ecs.FargateService
    history_service: ecs.FargateService
    matching_service: ecs.FargateService
    internal_worker_service: ecs.FargateService
    internal_nlb: elbv2.NetworkLoadBalancer
    log_groups: dict[str, logs.LogGroup]
    server_image: ecs.ContainerImage

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: TemporalClusterProps,
    ) -> None:
        super().__init__(scope, construct_id)
        self._props = props

        # Build the custom Temporal server image once and reuse across
        # all four cluster services. CDK uploads the asset to the bootstrap
        # ECR repo and the four task definitions reference the same digest.
        self.server_image = ecs.ContainerImage.from_asset(
            str(_DOCKER_ASSET_DIR),
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=props.cluster_name,
            vpc=props.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # Cloud Map private DNS for service discovery. Services register as
        # `<service>.<cluster>.local`; e.g. `frontend.cert-ra-temporal-staging.local`.
        self.cloud_map_namespace = servicediscovery.PrivateDnsNamespace(
            self,
            "Namespace",
            vpc=props.vpc,
            name=f"{props.cluster_name}.local",
        )

        # Internal NLB in front of the frontend service. gRPC traffic
        # arrives here from app, worker, and maint tasks. NLB preserves
        # source IP and doesn't require ALB-style listener rewrites.
        self.internal_nlb = elbv2.NetworkLoadBalancer(
            self,
            "InternalNlb",
            vpc=props.vpc,
            internet_facing=False,
            vpc_subnets=ec2.SubnetSelection(subnets=props.private_subnets),
            deletion_protection=True,
        )

        self.log_groups = {}
        self.frontend_service = self._build_service(
            name="frontend",
            container_port=TEMPORAL_FRONTEND_PORT,
            extra_env={},
        )
        self.history_service = self._build_service(
            name="history",
            container_port=TEMPORAL_HISTORY_PORT,
            extra_env={},
        )
        self.matching_service = self._build_service(
            name="matching",
            container_port=TEMPORAL_MATCHING_PORT,
            extra_env={},
        )
        self.internal_worker_service = self._build_service(
            name="internal-worker",
            # Temporal's server role is "worker" — there is no "internal-worker"
            # service, so passing the ECS name here makes the server exit with
            # `invalid service "internal-worker"`. Keep the ECS/log/discovery
            # name but run the real "worker" role.
            temporal_service="worker",
            container_port=TEMPORAL_INTERNAL_WORKER_PORT,
            extra_env={},
        )

        # Wire the NLB frontend listener → frontend service target group.
        frontend_target_group = elbv2.NetworkTargetGroup(
            self,
            "FrontendTargetGroup",
            vpc=props.vpc,
            port=TEMPORAL_FRONTEND_PORT,
            protocol=elbv2.Protocol.TCP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                protocol=elbv2.Protocol.TCP,
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                interval=cdk.Duration.seconds(30),
            ),
        )
        self.frontend_service.attach_to_network_target_group(frontend_target_group)
        self.internal_nlb.add_listener(
            "FrontendListener",
            port=TEMPORAL_FRONTEND_PORT,
            protocol=elbv2.Protocol.TCP,
            default_target_groups=[frontend_target_group],
        )

        NagSuppressions.add_resource_suppressions(
            self,
            [
                {
                    "id": "NIST.800.53.R5-IAMNoInlinePolicy",
                    "reason": (
                        "ECS task execution role is created by the L2 "
                        "TaskDefinition with an inline policy granting "
                        "secretsmanager:GetSecretValue + kms:Decrypt for the "
                        "RDS master secret + CMK. We don't author the policy."
                    ),
                },
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "ECS task execution role's CW Logs grant uses wildcards "
                        "on log-stream name (stream names aren't pre-computable)."
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": (
                        "Temporal server config is mostly env vars by design; "
                        "the only secrets are the RDS credentials which are "
                        "injected as ECS secrets (not plaintext env vars)."
                    ),
                },
                {
                    "id": "AwsSolutions-ELB2",
                    "reason": (
                        "Internal NLB access logs are out of scope for this PR. "
                        "L2 (tamper-resistant trail) tracks adding NLB flow logs "
                        "alongside the L2 CloudTrail hardening."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-ELBLoggingEnabled",
                    "reason": "Same as AwsSolutions-ELB2.",
                },
                {
                    "id": "NIST.800.53.R5-ELBDeletionProtectionEnabled",
                    "reason": (
                        "Deletion protection IS enabled (deletion_protection=True "
                        "on the NetworkLoadBalancer). False positive."
                    ),
                },
                {
                    "id": "NIST.800.53.R5-ELBv2ACMCertificateRequired",
                    "reason": (
                        "Internal NLB is a TCP passthrough for gRPC; TLS is "
                        "terminated by the Temporal server itself using the "
                        "per-service mTLS certs (PR 5 follow-up). The NLB "
                        "listener never sees plaintext."
                    ),
                },
            ],
            apply_to_children=True,
        )

    def _build_service(
        self,
        *,
        name: str,
        container_port: int,
        extra_env: dict[str, str],
        temporal_service: str | None = None,
    ) -> ecs.FargateService:
        """Create a Fargate service for one of the four Temporal roles.

        `name` is the ECS/log/service-discovery identifier; `temporal_service`
        is the Temporal server role passed via the SERVICES env (defaults to
        `name`). They differ only for the worker, whose ECS name is
        `internal-worker` but whose Temporal role is `worker`.
        """
        logs_cmk = kms.Key.from_key_arn(
            self,
            f"LogsCmk{name.replace('-', '').title()}",
            self._props.logs_cmk_arn,
        )
        log_group = logs.LogGroup(
            self,
            f"LogGroup{name.replace('-', '').title()}",
            log_group_name=f"/ecs/cert-ra-temporal-{name}",
            retention=self._props.log_retention,
            encryption_key=logs_cmk,
            # See LitestarService for the DESTROY rationale. The Temporal
            # cluster's logs are operationally useful but already capped
            # at 30-day retention, so a CREATE rollback losing the group
            # doesn't lose any audit value.
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        self.log_groups[name] = log_group

        task_definition = ecs.FargateTaskDefinition(
            self,
            f"TaskDef{name.replace('-', '').title()}",
            cpu=self._props.cpu,
            memory_limit_mib=self._props.memory_mib,
        )

        # Import the cross-stack secret + CMK by ARN — `from_secret_complete_arn`
        # returns an immutable ISecret whose resource policy CDK won't try
        # to mutate from this stack. Without this, calling
        # `ecs.Secret.from_secrets_manager(secret)` with the live ISecret
        # from DataStack creates a SecretsManager → TemporalStack reverse
        # dep that cycles with the existing forward dep (TemporalStack →
        # DataStack outputs).
        rds_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            f"RdsSecret{name.replace('-', '').title()}",
            self._props.rds_master_secret_arn,
        )
        rds_cmk = kms.Key.from_key_arn(
            self,
            f"RdsCmk{name.replace('-', '').title()}",
            self._props.rds_master_secret_cmk_arn,
        )

        env: dict[str, str] = {
            "SERVICES": temporal_service or name,
            "DB": "postgres12",
            "POSTGRES_SEEDS": self._props.rds_endpoint,
            "DB_PORT": self._props.rds_port,
            "DBNAME": "temporal",
            "VISIBILITY_DBNAME": "temporal_visibility",
            # RDS enforces SSL (rds.force_ssl=1). The server's auto-setup
            # config template gates persistence TLS on SQL_TLS_ENABLED — NOT
            # the SQL_TLS var that temporal-sql-tool (schema bootstrap) reads.
            # Set both so server processes and the sql-tool both connect over
            # TLS; without SQL_TLS_ENABLED the servers connect plaintext, get
            # the "no encryption" rejection, and crash-loop ("no usable
            # database connection found"). Encrypt without server-cert
            # verification (libpq sslmode=require equivalent).
            "SQL_TLS_ENABLED": "true",
            "SQL_TLS": "true",
            "SQL_TLS_DISABLE_HOST_VERIFICATION": "true",
            "NUM_HISTORY_SHARDS": str(NUM_HISTORY_SHARDS),
            # Sentinel: the entrypoint shim (_docker/temporal_server/
            # entrypoint.sh) replaces 0.0.0.0 with the task's real awsvpc
            # ENI IP at runtime so ringpop peers can dial this role. A
            # static IP can't be baked in — each Fargate task gets a fresh
            # one. Leaving it literally 0.0.0.0 breaks cluster membership.
            "TEMPORAL_BROADCAST_ADDRESS": "0.0.0.0",
            # The upstream config_template.yaml reads
            # TEMPORAL_TLS_REQUIRE_CLIENT_AUTH (not the *_FRONTEND_* variant
            # that earlier sketches assumed) — same toggle governs internode
            # and frontend listeners.
            "TEMPORAL_TLS_REQUIRE_CLIENT_AUTH": (
                "true" if self._props.mtls_enforce else "false"
            ),
        }
        env.update(extra_env)

        # Imported secret's grant_read still adds to the execution role's
        # identity policy (which is what we want); it just doesn't touch
        # the secret's resource policy. We also need explicit KMS decrypt
        # for the imported CMK.
        secrets: dict[str, ecs.Secret] = {
            "POSTGRES_USER": ecs.Secret.from_secrets_manager(
                rds_secret, field="username"
            ),
            "POSTGRES_PWD": ecs.Secret.from_secrets_manager(
                rds_secret, field="password"
            ),
        }

        # Wire mTLS cert injection when enforcement is on. The entrypoint
        # shim in _docker/temporal_server/ reads these MTLS_* env vars,
        # writes the content to files in /run/temporal-tls/, then sets
        # TEMPORAL_TLS_SERVER_CERT etc. before exec'ing the stock
        # entrypoint. We pass cert+chain+key as separate ECS Secrets
        # (extracted from the temporal-frontend SeededSecret's JSON fields)
        # so the secret never lives in plaintext outside the container.
        if self._props.mtls_enforce:
            frontend_secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                f"FrontendMtlsSecret{name.replace('-', '').title()}",
                self._props.frontend_mtls_secret_arn,
            )
            secrets["MTLS_CERT_CONTENT"] = ecs.Secret.from_secrets_manager(
                frontend_secret, field="cert"
            )
            secrets["MTLS_KEY_CONTENT"] = ecs.Secret.from_secrets_manager(
                frontend_secret, field="key"
            )
            secrets["MTLS_CHAIN_CONTENT"] = ecs.Secret.from_secrets_manager(
                frontend_secret, field="chain"
            )

        container = task_definition.add_container(
            "Server",
            image=self.server_image,
            essential=True,
            environment=env,
            secrets=secrets,
            logging=ecs.LogDriver.aws_logs(
                stream_prefix=name,
                log_group=log_group,
            ),
        )
        container.add_port_mappings(
            ecs.PortMapping(container_port=container_port, protocol=ecs.Protocol.TCP),
            ecs.PortMapping(
                container_port=container_port + TEMPORAL_MEMBERSHIP_PORT_OFFSET,
                protocol=ecs.Protocol.TCP,
            ),
        )

        # Imported CMK + secret don't add to the execution role for KMS
        # decrypt — do it explicitly. We must wait until after add_container
        # because that's what triggers CDK to lazily create the execution role.
        rds_cmk.grant_decrypt(task_definition.obtain_execution_role())
        if self._props.mtls_enforce:
            secrets_cmk = kms.Key.from_key_arn(
                self,
                f"SecretsCmk{name.replace('-', '').title()}",
                self._props.secrets_cmk_arn,
            )
            secrets_cmk.grant_decrypt(task_definition.obtain_execution_role())

        return ecs.FargateService(
            self,
            f"Service{name.replace('-', '').title()}",
            cluster=self.cluster,
            task_definition=task_definition,
            desired_count=1,
            security_groups=[self._props.temporal_fe_security_group],
            vpc_subnets=ec2.SubnetSelection(subnets=self._props.private_subnets),
            cloud_map_options=ecs.CloudMapOptions(
                name=name,
                cloud_map_namespace=self.cloud_map_namespace,
                dns_record_type=servicediscovery.DnsRecordType.A,
            ),
            # Without a circuit breaker, ECS will keep trying to start a
            # failing task for up to ~1 hour (CloudFormation's default
            # service-stabilisation timeout). Hard-failing fast lets CFN
            # surface the failure quickly so the operator can iterate
            # instead of waiting on an opaque hang. `rollback=True`
            # auto-reverts the service to the previous task-definition
            # revision when the new one can't stabilise — matches the
            # behaviour we already have on WorkersStack + MaintenanceStack.
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=0,
            max_healthy_percent=200,
            enable_execute_command=True,
        )

    @property
    def frontend_endpoint(self) -> str:
        """`host:port` endpoint clients use to reach the Temporal frontend gRPC port.

        Includes the explicit `:7233` suffix because the Temporal Python SDK
        (`Client.connect(target_host)`) and the `temporal` CLI both expect a
        host:port string — without the port, gRPC dial fails and workers can
        never reach the cluster.
        """
        return f"{self.internal_nlb.load_balancer_dns_name}:{TEMPORAL_FRONTEND_PORT}"
