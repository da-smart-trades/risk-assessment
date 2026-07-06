# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import (
    ObservabilityStack,
    ObservabilityStackProps,
)
from cert_ra_infra.stacks.secrets import SecretsStack, SecretsStackProps
from cert_ra_infra.stacks.temporal import TemporalStack, TemporalStackProps

_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _synth_stack(
    env_name: str = "staging", mtls_enforce: bool = True
) -> assertions.Template:
    app = cdk.App()
    cfg = load_env(env_name)
    env = cdk.Environment(account="111111111111", region=cfg.region)
    network = NetworkStack(
        app, f"CertRa-NetworkStack-{cfg.env}", env=env, env_config=cfg
    )
    data = DataStack(
        app,
        f"CertRa-DataStack-{cfg.env}",
        env=env,
        env_config=cfg,
        data_props=DataStackProps(
            network=network, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    secrets = SecretsStack(
        app,
        f"CertRa-SecretsStack-{cfg.env}",
        env=env,
        env_config=cfg,
        secrets_props=SecretsStackProps(installer_role_arn_pattern=_INSTALLER_ARN),
    )
    observability = ObservabilityStack(
        app,
        f"CertRa-ObservabilityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        observability_props=ObservabilityStackProps(
            data=data, installer_role_arn_pattern=_INSTALLER_ARN
        ),
    )
    stack = TemporalStack(
        app,
        f"CertRa-TemporalStack-{cfg.env}",
        env=env,
        env_config=cfg,
        mtls_enforce=mtls_enforce,
        temporal_props=TemporalStackProps(
            secrets=secrets,
            network=network,
            data=data,
            observability=observability,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_stack_creates_root_and_subordinate_cas() -> None:
    template = _synth_stack()
    template.resource_count_is("AWS::ACMPCA::CertificateAuthority", 2)


def test_staging_root_ca_uses_staging_common_name() -> None:
    template = _synth_stack("staging")
    cas = template.find_resources(
        "AWS::ACMPCA::CertificateAuthority", {"Properties": {"Type": "ROOT"}}
    )
    (root,) = cas.values()
    assert (
        root["Properties"]["Subject"]["CommonName"] == "cert-ra-temporal-root-staging"
    )


def test_prod_root_ca_uses_prod_common_name() -> None:
    """A5: per-env CAs — staging and prod have different roots."""
    template = _synth_stack("prod")
    cas = template.find_resources(
        "AWS::ACMPCA::CertificateAuthority", {"Properties": {"Type": "ROOT"}}
    )
    (root,) = cas.values()
    assert root["Properties"]["Subject"]["CommonName"] == "cert-ra-temporal-root-prod"


def test_stack_exports_ca_arns() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    assert "RootCaArn" in outputs
    assert "SubordinateCaArn" in outputs


def test_stack_creates_initial_cert_issuance_custom_resource() -> None:
    """The B1 Custom Resource that populates the SeededSecret mTLS shells."""
    template = _synth_stack()
    template.resource_count_is("Custom::TemporalMtlsInitialCertIssuance", 1)


def test_initial_cert_issuance_targets_all_services() -> None:
    template = _synth_stack()
    crs = template.find_resources("Custom::TemporalMtlsInitialCertIssuance")
    (cr,) = crs.values()
    services = cr["Properties"]["Services"]
    names = {svc["Name"] for svc in services}
    assert names == {
        "temporal-frontend",
        "worker-metrics",
        "worker-alerts",
        "internal-worker",
        "maint",
        "app",
    }


def test_initial_cert_issuance_service_common_names_use_cert_ra_local_suffix() -> None:
    template = _synth_stack()
    crs = template.find_resources("Custom::TemporalMtlsInitialCertIssuance")
    (cr,) = crs.values()
    for svc in cr["Properties"]["Services"]:
        assert svc["CommonName"].endswith(".cert-ra.local"), (
            f"Unexpected CN: {svc['CommonName']}"
        )


def test_stack_creates_cert_renewal_eventbridge_rule() -> None:
    """PR 3: daily EventBridge schedule that triggers the renewal Lambda."""
    template = _synth_stack()
    template.resource_count_is("AWS::Events::Rule", 1)


def test_stack_exports_cert_renewal_handler_arn() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    assert "CertRenewalHandlerArn" in outputs


def test_stack_creates_temporal_ecs_cluster() -> None:
    """PR 4: Temporal services live in a dedicated ECS cluster."""
    template = _synth_stack("staging")
    template.has_resource_properties(
        "AWS::ECS::Cluster", {"ClusterName": "cert-ra-temporal-staging"}
    )


def test_stack_creates_four_fargate_services() -> None:
    """One Fargate service per Temporal role: frontend, history, matching, internal-worker."""
    template = _synth_stack()
    template.resource_count_is("AWS::ECS::Service", 4)


def test_stack_creates_internal_nlb_for_frontend() -> None:
    template = _synth_stack()
    template.has_resource_properties(
        "AWS::ElasticLoadBalancingV2::LoadBalancer",
        {"Type": "network", "Scheme": "internal"},
    )


def test_stack_exports_frontend_endpoint() -> None:
    template = _synth_stack()
    outputs = template.find_outputs("*")
    assert "TemporalFrontendEndpoint" in outputs
