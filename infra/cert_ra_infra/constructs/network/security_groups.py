# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from cdk_nag import NagSuppressions
from constructs import Construct

# AWS-managed prefix lists for the S3 gateway endpoint, keyed by region.
# Gateway endpoints route via the prefix list at the route-table layer,
# but security groups with `allow_all_outbound=False` still need an
# explicit egress rule against the prefix list — otherwise SYNs are
# dropped before the route table sees them.
#
# These IDs are AWS-managed and stable per region; they only change if
# AWS rebuilds the prefix list (rare; announced ahead of time). Add new
# regions here when cert-ra grows beyond us-east-2.
_S3_GATEWAY_PREFIX_LIST_BY_REGION: dict[str, str] = {
    "us-east-2": "pl-7ba54012",
    "us-east-1": "pl-63a5400a",
    "us-west-2": "pl-68a54001",
}


@dataclass(frozen=True, slots=True)
class CertRaSecurityGroupsProps:
    """Props for CertRaSecurityGroups.

    Per § Resource ownership matrix in the design spec, every role gets its
    own SG (never shared). Ingress rules are added on the *target* SG
    (e.g. `rds_sg.add_ingress_rule(app_sg, 5432)`) by the consumer construct.
    """

    vpc: ec2.IVpc


class CertRaSecurityGroups(Construct):
    """Factory for the per-role security groups.

    Public attributes:
    - `alb`: internet-facing ALB. Ingress 443 from 0.0.0.0/0.
    - `app`: Litestar Fargate service. Ingress from `alb` on 8000.
    - `worker`: workers (metrics + alerts). No ingress.
    - `temporal_fe`: Temporal frontend. Ingress on 7233 from app, worker, maint.
    - `maint`: maintenance container. **No 0.0.0.0/0 egress (H2-A).** No ingress.
    - `migrate`: one-off migration runner. No ingress.
    - `rds`: RDS Postgres. Ingress on 5432 from app, worker, maint, migrate.

    The construct only **creates** the SGs and the most-trivial structural
    rules (e.g. ALB 443 from anywhere). Service-to-service ingress is wired
    by the consumer constructs because the source SG isn't always known at
    NetworkStack-deploy time.
    """

    alb: ec2.SecurityGroup
    app: ec2.SecurityGroup
    worker: ec2.SecurityGroup
    temporal_fe: ec2.SecurityGroup
    maint: ec2.SecurityGroup
    migrate: ec2.SecurityGroup
    rds: ec2.SecurityGroup

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: CertRaSecurityGroupsProps,
    ) -> None:
        super().__init__(scope, construct_id)

        # ALB — public-facing; CDK default egress to anywhere is fine.
        self.alb = ec2.SecurityGroup(
            self,
            "AlbSg",
            vpc=props.vpc,
            description="cert-ra: public ALB",
            security_group_name="cert-ra-alb-sg",
            allow_all_outbound=True,
        )
        self.alb.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            description="HTTPS from anywhere",
        )
        NagSuppressions.add_resource_suppressions(
            self.alb,
            [
                {
                    "id": "AwsSolutions-EC23",
                    "reason": (
                        "Public ALB by design accepts HTTPS from 0.0.0.0/0. "
                        "Port restricted to 443 only; no SSH/other ports open. "
                        "Authentication and WAF (deferred) provide the next layer."
                    ),
                },
            ],
        )

        # App Litestar service — gets traffic from the ALB only.
        self.app = ec2.SecurityGroup(
            self,
            "AppSg",
            vpc=props.vpc,
            description="cert-ra: Litestar app Fargate service",
            security_group_name="cert-ra-app-sg",
            allow_all_outbound=True,
        )

        # Workers — no ingress (they call OUT to Temporal); outbound for RPC etc.
        self.worker = ec2.SecurityGroup(
            self,
            "WorkerSg",
            vpc=props.vpc,
            description="cert-ra: metrics/alerts workers",
            security_group_name="cert-ra-worker-sg",
            allow_all_outbound=True,
        )

        # Temporal frontend — others call IN on 7233.
        self.temporal_fe = ec2.SecurityGroup(
            self,
            "TemporalFeSg",
            vpc=props.vpc,
            description="cert-ra: Temporal frontend (gRPC :7233)",
            security_group_name="cert-ra-temporal-fe-sg",
            allow_all_outbound=True,
        )

        # Maint container — H2-A: NO 0.0.0.0/0 egress.
        # `allow_all_outbound=False` means we explicitly opt-in to each
        # egress destination. Egress to RDS, Temporal, and VPC endpoints is
        # added by consumers; nothing else is reachable.
        self.maint = ec2.SecurityGroup(
            self,
            "MaintSg",
            vpc=props.vpc,
            description=(
                "cert-ra: maintenance container. H2-A - no 0.0.0.0/0 egress; "
                "only RDS, Temporal, and VPC endpoints reachable."
            ),
            security_group_name="cert-ra-maint-sg",
            allow_all_outbound=False,
        )

        # Migration runner — outbound to RDS only; allow_all_outbound for VPC endpoints.
        self.migrate = ec2.SecurityGroup(
            self,
            "MigrateSg",
            vpc=props.vpc,
            description="cert-ra: one-off Alembic migration task",
            security_group_name="cert-ra-migrate-sg",
            allow_all_outbound=True,
        )

        # RDS — ingress from app, worker, maint, migrate; no outbound.
        self.rds = ec2.SecurityGroup(
            self,
            "RdsSg",
            vpc=props.vpc,
            description="cert-ra: RDS Postgres",
            security_group_name="cert-ra-rds-sg",
            allow_all_outbound=False,
        )
        self.rds.add_ingress_rule(
            self.app,
            ec2.Port.tcp(5432),
            description="App to RDS",
        )
        self.rds.add_ingress_rule(
            self.worker,
            ec2.Port.tcp(5432),
            description="Workers to RDS",
        )
        self.rds.add_ingress_rule(
            self.maint,
            ec2.Port.tcp(5432),
            description="Maintenance to RDS",
        )
        self.rds.add_ingress_rule(
            self.migrate,
            ec2.Port.tcp(5432),
            description="Migration runner to RDS",
        )
        self.rds.add_ingress_rule(
            self.temporal_fe,
            ec2.Port.tcp(5432),
            description="Temporal cluster + schema bootstrap to RDS",
        )

        # Temporal intra-cluster membership. All four server roles
        # (frontend/history/matching/internalworker) run as separate ECS
        # services sharing this SG. They form a ringpop ring and call each
        # other's gRPC listeners, so the SG must allow traffic from itself:
        #   6933-6939  ringpop membership/gossip (one port per role)
        #   7233-7239  gRPC (7233 frontend, 7234 history, 7235 matching,
        #              7239 worker, 7236 internal-frontend)
        # Without these the roles can't discover/reach each other and every
        # role crash-loops on "Not enough hosts to serve the request".
        self.temporal_fe.add_ingress_rule(
            self.temporal_fe,
            ec2.Port.tcp_range(6933, 6939),
            description="Temporal intra-cluster ringpop membership/gossip",
        )
        self.temporal_fe.add_ingress_rule(
            self.temporal_fe,
            ec2.Port.tcp_range(7233, 7239),
            description="Temporal intra-cluster gRPC (all roles)",
        )

        # The internal NLB that fronts the Temporal frontend uses IP targets
        # with preserve_client_ip disabled, so BOTH its health-check probes
        # and the proxied data traffic arrive sourced from the NLB's ENI IPs
        # (in the VPC private subnets) — not from the app/worker/maint SGs
        # below. The NLB has no SG of its own, so allow the frontend gRPC
        # port from the VPC CIDR. Without this the NLB health check on 7233
        # times out, the target never goes healthy, and the frontend ECS
        # service never reaches steady state (stack hangs in CREATE). The
        # frontend is internal-only (internal NLB, private VPC), so
        # VPC-scoped ingress on 7233 is acceptable.
        self.temporal_fe.add_ingress_rule(
            ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
            ec2.Port.tcp(7233),
            description="Internal NLB health check + proxied traffic to Temporal frontend",
        )

        # Temporal frontend ingress.
        self.temporal_fe.add_ingress_rule(
            self.app,
            ec2.Port.tcp(7233),
            description="App to Temporal frontend",
        )
        self.temporal_fe.add_ingress_rule(
            self.worker,
            ec2.Port.tcp(7233),
            description="Workers to Temporal frontend",
        )
        self.temporal_fe.add_ingress_rule(
            self.maint,
            ec2.Port.tcp(7233),
            description="Maintenance to Temporal frontend",
        )

        # Maint egress to RDS + Temporal (explicit, no NAT path).
        self.maint.add_egress_rule(
            self.rds,
            ec2.Port.tcp(5432),
            description="Maint to RDS",
        )
        self.maint.add_egress_rule(
            self.temporal_fe,
            ec2.Port.tcp(7233),
            description="Maint to Temporal frontend (direct task)",
        )
        # Maint also needs to reach the internal NLB that fronts the
        # Temporal frontend service. The NLB's ENIs have no security
        # group (we don't pass `security_groups=` to the construct), so
        # the SG-to-SG egress rule above doesn't match NLB-bound traffic.
        # Allow TCP 7233 to the VPC CIDR — the only thing listening on
        # that port inside the VPC is Temporal, so the broader scope is
        # equivalent in practice.
        self.maint.add_egress_rule(
            ec2.Peer.ipv4(props.vpc.vpc_cidr_block),
            ec2.Port.tcp(7233),
            description="Maint to Temporal frontend (via internal NLB)",
        )
        # Egress to S3 (via the S3 gateway endpoint). ECR layer pulls
        # download blob content from `prod-<region>-starport-layer-bucket`
        # in S3 — the EcrApi/EcrDkr interface endpoints serve manifests
        # but the actual layer data goes through S3. Without this rule
        # the SG drops the SYN before the gateway endpoint can route it,
        # and the maint task crash-loops with CannotPullContainerError.
        region = cdk.Stack.of(self).region
        s3_prefix_list = _S3_GATEWAY_PREFIX_LIST_BY_REGION.get(region)
        if s3_prefix_list is None:
            msg = (
                f"No S3 gateway prefix list mapping for region {region!r}; "
                f"add it to _S3_GATEWAY_PREFIX_LIST_BY_REGION."
            )
            raise ValueError(msg)
        self.maint.add_egress_rule(
            ec2.Peer.prefix_list(s3_prefix_list),
            ec2.Port.tcp(443),
            description="Maint to S3 (ECR image layer pulls via gateway endpoint)",
        )

        # App ingress from ALB on the Litestar container port.
        self.app.add_ingress_rule(
            self.alb,
            ec2.Port.tcp(8000),
            description="ALB to App container",
        )
