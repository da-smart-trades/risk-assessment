# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aws_cdk as cdk
from aws_cdk import Stack
from constructs import Construct

from cert_ra_infra.constructs.temporal.cert_issuance import (
    InitialCertIssuance,
    InitialCertIssuanceProps,
    TemporalServiceCertConfig,
)
from cert_ra_infra.constructs.temporal.cert_renewal import (
    CertRenewal,
    CertRenewalProps,
)
from cert_ra_infra.constructs.temporal.cluster import (
    TemporalCluster,
    TemporalClusterProps,
)
from cert_ra_infra.constructs.temporal.mtls_pki import (
    TemporalMtlsPki,
    TemporalMtlsPkiProps,
)
from cert_ra_infra.constructs.temporal.root_ca_disable import (
    RootCaDisable,
    RootCaDisableProps,
)
from cert_ra_infra.constructs.temporal.schema_bootstrap import (
    TemporalSchemaBootstrap,
    TemporalSchemaBootstrapProps,
)
from cert_ra_infra.stacks._config import EnvConfig
from cert_ra_infra.stacks.data import DataStack
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import ObservabilityStack
from cert_ra_infra.stacks.secrets import SecretsStack

DEFAULT_MTLS_DNS_SUFFIX = "cert-ra.local"
_MTLS_SERVICE_NAMES = (
    "temporal-frontend",
    "worker-metrics",
    "worker-alerts",
    "internal-worker",
    "maint",
    # Keep in sync with `SecretsStack._TEMPORAL_MTLS_SERVICES`. The
    # CommonName for the app cert is `app.cert-ra.local`; the frontend
    # rejects connections whose client cert chains here are missing.
    "app",
)


@dataclass(frozen=True, slots=True)
class TemporalStackProps:
    """Stack-level inputs for TemporalStack.

    Cross-stack refs:
    - `secrets`: provides the five SeededSecret mTLS shells the
      InitialCertIssuance Custom Resource populates and the CertRenewal
      Lambda refreshes.
    - `network`: VPC, private subnets, temporal-fe-sg used by the
      Fargate services and internal NLB.
    - `data`: RDS endpoint and master credential secret used by the
      Temporal server for persistence.
    - `observability`: cert-ra-logs-cmk for CloudWatch log group encryption.
    """

    secrets: SecretsStack
    network: NetworkStack
    data: DataStack
    observability: ObservabilityStack
    mtls_dns_suffix: str = DEFAULT_MTLS_DNS_SUFFIX
    """Service certs use CommonName `<service>.<suffix>`."""


class TemporalStack(Stack):
    """Self-hosted Temporal cluster (FE / History / Matching / Internal
    Worker) plus the mTLS PKI that enforces client authentication on the
    frontend.

    Scope after PR 6:
    - mTLS PKI shell (PR 1): per-environment root + subordinate ACM
      Private CAs
    - InitialCertIssuance Custom Resource (PR 2 / B1 path 1): a
      Lambda that issues end-entity certs from the subordinate CA and
      writes them into the per-service SeededSecret shells from
      SecretsStack — runs once on stack create, no-op on update
    - CertRenewal (PR 3 / B1 path 2): a daily EventBridge-scheduled
      Lambda that reads each cert from Secrets Manager, parses the
      expiration date, and re-issues if within 159 days of expiry.
    - TemporalCluster (PR 4): ECS Fargate cluster running the four
      Temporal server roles (Frontend / History / Matching / Internal-
      Worker) with Cloud Map service discovery, internal NLB in front
      of the frontend, and RDS persistence wired from DataStack.
    - **PR 5 cleanup**: logs CMK wired into per-service CW log groups;
      TemporalSchemaBootstrap one-off Fargate task; RootCaDisable B2
      Custom Resource.
    - **PR 6 mTLS cert injection**: the cluster now runs a custom
      Docker image (built from `_docker/temporal_server/`) that
      materialises the `temporal-frontend` SeededSecret's
      cert/key/chain into `/run/temporal-tls/` files before exec'ing
      the upstream Temporal entrypoint. When mTLS enforcement is on,
      the cluster depends on InitialCertIssuance so the certs are
      populated before the first task starts.

    Follow-up PRs (in MaintenanceStack / WorkersStack) wire the
    worker- and maint-side client certs from the remaining four
    SeededSecret shells (`worker-metrics`, `worker-alerts`,
    `internal-worker`, `maint`).
    """

    mtls_pki: TemporalMtlsPki
    initial_cert_issuance: InitialCertIssuance
    cert_renewal: CertRenewal
    cluster: TemporalCluster
    schema_bootstrap: TemporalSchemaBootstrap
    root_ca_disable: RootCaDisable

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_config: EnvConfig,
        mtls_enforce: bool,
        temporal_props: TemporalStackProps,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.env_config = env_config
        self.mtls_enforce = mtls_enforce

        self.mtls_pki = TemporalMtlsPki(
            self,
            "MtlsPki",
            props=TemporalMtlsPkiProps(env_name=env_config.env),
        )

        # Build the service → secret mapping. Service names match
        # SecretsStack._TEMPORAL_MTLS_SERVICES; CommonNames follow
        # `<name>.<suffix>` so the Temporal frontend's TLS config can
        # validate them via `serverName`. We pass ARNs/names as strings
        # (not ISecret objects) to avoid a cross-stack cycle — see the
        # InitialCertIssuance docstring for reasoning.
        suffix = temporal_props.mtls_dns_suffix
        services = [
            TemporalServiceCertConfig(
                name=name,
                secret_arn=temporal_props.secrets.temporal_mtls_secrets[
                    name
                ].secret_arn,
                secret_name=temporal_props.secrets.temporal_mtls_secrets[
                    name
                ].secret_name,
                common_name=f"{name}.{suffix}",
            )
            for name in _MTLS_SERVICE_NAMES
        ]

        logs_cmk_arn = temporal_props.observability.logs_cmk.key.key_arn
        rds_secret_arn = temporal_props.data.postgres.master_secret_arn
        rds_cmk_arn = temporal_props.data.rds_cmk.key.key_arn

        self.initial_cert_issuance = InitialCertIssuance(
            self,
            "InitialCertIssuance",
            props=InitialCertIssuanceProps(
                subordinate_ca_arn=self.mtls_pki.subordinate_ca_arn,
                services=services,
                secrets_cmk_arn=temporal_props.secrets.secrets_cmk.key.key_arn,
            ),
        )
        # Cert issuance depends on the subordinate CA being activated.
        # CDK already infers this from the ARN reference, but make it
        # explicit so the dependency graph is obvious.
        self.initial_cert_issuance.custom_resource.node.add_dependency(
            self.mtls_pki.subordinate_activation
        )

        # Renewal Lambda runs daily and re-issues certs nearing expiry.
        # Shares the same TemporalServiceCertConfig list as the initial
        # Custom Resource — same five services, same PCA, same secrets.
        self.cert_renewal = CertRenewal(
            self,
            "CertRenewal",
            props=CertRenewalProps(
                subordinate_ca_arn=self.mtls_pki.subordinate_ca_arn,
                services=services,
                secrets_cmk_arn=temporal_props.secrets.secrets_cmk.key.key_arn,
            ),
        )
        # The renewal Lambda should never run before the initial issuance
        # populates the secrets. Without this, the renewal Lambda could
        # fire on day one before the InitialCertIssuance Custom Resource
        # completes and would unnecessarily re-issue every cert.
        self.cert_renewal.node.add_dependency(self.initial_cert_issuance)

        # The Temporal Fargate cluster. Cert injection from the
        # SeededSecret shells is a follow-up PR; for now mtls_enforce
        # only gates the env var that toggles client-auth requirement.
        # The frontend cert is the shared cert across all four cluster
        # services (internode + frontend TLS use the same identity per
        # the design). Pulled from the SeededSecret populated by
        # InitialCertIssuance.
        frontend_mtls_secret_arn = temporal_props.secrets.temporal_mtls_secrets[
            "temporal-frontend"
        ].secret_arn
        secrets_cmk_arn = temporal_props.secrets.secrets_cmk.key.key_arn

        self.cluster = TemporalCluster(
            self,
            "Cluster",
            props=TemporalClusterProps(
                cluster_name=f"cert-ra-temporal-{env_config.env}",
                vpc=temporal_props.network.vpc.vpc,
                private_subnets=temporal_props.network.vpc.private_egress_subnets,
                temporal_fe_security_group=temporal_props.network.security_groups.temporal_fe,
                alb_security_group=temporal_props.network.security_groups.temporal_fe,
                rds_endpoint=temporal_props.data.postgres.endpoint_address,
                rds_port=temporal_props.data.postgres.endpoint_port,
                rds_master_secret_arn=rds_secret_arn,
                rds_master_secret_cmk_arn=rds_cmk_arn,
                logs_cmk_arn=logs_cmk_arn,
                mtls_enforce=mtls_enforce,
                frontend_mtls_secret_arn=frontend_mtls_secret_arn,
                secrets_cmk_arn=secrets_cmk_arn,
            ),
        )
        # When mTLS enforcement is on, the cluster's task definitions
        # reference the temporal-frontend SeededSecret's `cert`/`key`/`chain`
        # fields. CDK already infers the resource dep from the secret ARN,
        # but the cluster must not start before the Custom Resource has
        # actually populated those fields with real PEM content (the
        # SeededSecret's placeholder is an empty JSON object, which would
        # surface as an empty file mount and crash the server).
        if mtls_enforce:
            self.cluster.node.add_dependency(self.initial_cert_issuance)

        # One-off task definition for schema setup. Operators invoke via
        # `aws ecs run-task` after deploy (initial-setup.sh step 12).
        self.schema_bootstrap = TemporalSchemaBootstrap(
            self,
            "SchemaBootstrap",
            props=TemporalSchemaBootstrapProps(
                cluster=self.cluster.cluster,
                vpc=temporal_props.network.vpc.vpc,
                private_subnets=temporal_props.network.vpc.private_egress_subnets,
                security_group=temporal_props.network.security_groups.temporal_fe,
                rds_endpoint=temporal_props.data.postgres.endpoint_address,
                rds_port=temporal_props.data.postgres.endpoint_port,
                rds_master_secret_arn=rds_secret_arn,
                rds_master_secret_cmk_arn=rds_cmk_arn,
                logs_cmk_arn=logs_cmk_arn,
            ),
        )

        # B2: disable the root CA once the subordinate is in place. Runs
        # after subordinate activation; idempotent on re-runs.
        self.root_ca_disable = RootCaDisable(
            self,
            "RootCaDisable",
            props=RootCaDisableProps(root_ca_arn=self.mtls_pki.root_ca_arn),
        )
        self.root_ca_disable.custom_resource.node.add_dependency(
            self.mtls_pki.subordinate_activation
        )
        # The initial cert issuance also needs the root CA to still be
        # active (it issues certs via the subordinate, not the root, but
        # the subordinate's cert chains to the root and PCA may validate
        # the chain). Hold off the disable until issuance completes.
        self.root_ca_disable.custom_resource.node.add_dependency(
            self.initial_cert_issuance
        )

        cdk.CfnOutput(
            self,
            "RootCaArn",
            value=self.mtls_pki.root_ca_arn,
            export_name=f"{self.stack_name}-RootCaArn",
        )
        cdk.CfnOutput(
            self,
            "SubordinateCaArn",
            value=self.mtls_pki.subordinate_ca_arn,
            export_name=f"{self.stack_name}-SubordinateCaArn",
        )
        cdk.CfnOutput(
            self,
            "CertRenewalHandlerArn",
            value=self.cert_renewal.handler_function_arn,
            export_name=f"{self.stack_name}-CertRenewalHandlerArn",
        )
        cdk.CfnOutput(
            self,
            "TemporalFrontendEndpoint",
            value=self.cluster.frontend_endpoint,
            export_name=f"{self.stack_name}-TemporalFrontendEndpoint",
        )
        cdk.CfnOutput(
            self,
            "SchemaBootstrapTaskFamily",
            value=self.schema_bootstrap.task_family,
            export_name=f"{self.stack_name}-SchemaBootstrapTaskFamily",
        )
