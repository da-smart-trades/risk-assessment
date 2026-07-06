# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from constructs import Construct

from cert_ra_infra.constructs.ops.maintenance_container import (
    MaintenanceContainer,
    MaintenanceContainerProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import ObservabilityStack
from cert_ra_infra.stacks.secrets import SecretsStack
from cert_ra_infra.stacks.temporal import TemporalStack

# SNI / cert-CN the maint container validates against the Temporal
# frontend cert. Matches WorkersStack + TemporalStack.
_TEMPORAL_FRONTEND_SNI = "temporal-frontend.cert-ra.local"


@dataclass(frozen=True, slots=True)
class MaintenanceStackProps:
    """Stack-level inputs for MaintenanceStack."""

    network: NetworkStack
    data: DataStack
    secrets: SecretsStack
    observability: ObservabilityStack
    temporal: TemporalStack
    # No image_tag, no IdentityStack ref — the maint image is a CDK
    # Docker asset built locally from `_docker/maint/Dockerfile`; it
    # ships independently of the cert-ra app image and doesn't pull
    # from the IdentityStack ECR repo.


class MaintenanceStack(Stack):
    """Always-on operator-facing maintenance task.

    Per the design spec § MaintenanceContainer construct:
    - **A1**: dedicated `cert-ra-maint-${env}` ECS cluster — separate
      from AppStack / WorkersStack / MigrationsStack clusters. This
      is the cluster ARN boundary that scopes Upgrader's
      `ecs:ExecuteCommand` permission; Upgrader cannot Exec into
      app/worker tasks because they live in a different cluster.
    - One `MaintenanceContainer` instance with `desired_count=1`
      and `enable_execute_command=True`. Container runs `sleep
      infinity`; operators invoke `aws ecs execute-command` to drop
      into a shell.
    - Task role inherits A4 worker-mTLS deny + ECS Exec channel
      permissions from the construct.

    CFN outputs match what `aws ecs execute-command` needs:
    - `ClusterName` — `cert-ra-maint-${env}`
    - `ServiceName` — for picking up the running task ARN
    - `TaskRoleArn` — useful for the Upgrader IAM trust diff
    """

    cluster: ecs.Cluster
    container: MaintenanceContainer

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        maintenance_props: MaintenanceStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"cert-ra-maint-{env_config.env}",
            vpc=maintenance_props.network.vpc.vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # Collect VPC interface endpoint SGs so the maint SG can be
        # opened up to 443 against each. The maint SG itself has
        # allow_all_outbound=False; without these egress rules AWS
        # SDK calls from the container would be dropped at the SG
        # layer even though VPC routing would send them to the
        # endpoint ENIs.
        vpc_endpoint_sgs: list[ec2.ISecurityGroup] = []
        for endpoint in maintenance_props.network.vpc.interface_endpoints.values():
            vpc_endpoint_sgs.extend(_security_groups_of(endpoint))

        secret_key = "maint"
        maint_mtls_secret = maintenance_props.secrets.temporal_mtls_secrets[secret_key]

        self.container = MaintenanceContainer(
            self,
            "Container",
            props=MaintenanceContainerProps(
                service_name=f"cert-ra-maint-{env_config.env}",
                env_name=env_config.env,
                cluster=self.cluster,
                vpc=maintenance_props.network.vpc.vpc,
                private_subnets=maintenance_props.network.vpc.private_egress_subnets,
                maint_security_group=maintenance_props.network.security_groups.maint,
                vpc_endpoint_security_groups=vpc_endpoint_sgs,
                rds_master_secret_arn=(
                    maintenance_props.data.postgres.master_secret_arn
                ),
                rds_master_secret_cmk_arn=(maintenance_props.data.rds_cmk.key.key_arn),
                rds_endpoint=maintenance_props.data.postgres.endpoint_address,
                rds_port=maintenance_props.data.postgres.endpoint_port,
                secrets_cmk_arn=(maintenance_props.secrets.secrets_cmk.key.key_arn),
                maint_mtls_secret_arn=maint_mtls_secret.secret_arn,
                temporal_frontend_endpoint=(
                    maintenance_props.temporal.cluster.frontend_endpoint
                ),
                temporal_tls_server_name=_TEMPORAL_FRONTEND_SNI,
                logs_cmk_arn=(maintenance_props.observability.logs_cmk.key.key_arn),
            ),
        )

        cdk.CfnOutput(
            self,
            "ClusterName",
            value=self.cluster.cluster_name,
            export_name=f"{self.stack_name}-ClusterName",
        )
        cdk.CfnOutput(
            self,
            "ServiceName",
            value=self.container.service.service_name,
            export_name=f"{self.stack_name}-ServiceName",
        )
        cdk.CfnOutput(
            self,
            "TaskRoleArn",
            value=self.container.task_role_arn,
            export_name=f"{self.stack_name}-TaskRoleArn",
        )


def _security_groups_of(
    endpoint: ec2.InterfaceVpcEndpoint,
) -> list[ec2.ISecurityGroup]:
    """Return the SGs attached to a CDK-created InterfaceVpcEndpoint.

    CDK's `InterfaceVpcEndpoint` exposes `connections.security_groups`
    on the L2; that's the canonical accessor. Isolated in a helper so
    MaintenanceStack's main flow stays linear.
    """
    return list(endpoint.connections.security_groups)
