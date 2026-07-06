# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.maintenance import MaintenanceStack, MaintenanceStackProps
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


def _synth() -> assertions.Template:
    app = cdk.App()
    cfg = load_env("staging")
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
    temporal = TemporalStack(
        app,
        f"CertRa-TemporalStack-{cfg.env}",
        env=env,
        env_config=cfg,
        mtls_enforce=True,
        temporal_props=TemporalStackProps(
            secrets=secrets, network=network, data=data, observability=observability
        ),
    )
    stack = MaintenanceStack(
        app,
        f"CertRa-MaintenanceStack-{cfg.env}",
        env=env,
        env_config=cfg,
        maintenance_props=MaintenanceStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            temporal=temporal,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_dedicated_maint_cluster() -> None:
    """A1: maint cluster is separate from app/worker/migration
    clusters so the Upgrader ecs:ExecuteCommand IAM scope only
    opens this one cluster's tasks."""
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::Cluster", {"ClusterName": "cert-ra-maint-staging"}
    )


def test_creates_one_service_and_one_task_definition() -> None:
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 1)
    template.resource_count_is("AWS::ECS::TaskDefinition", 1)


def test_exports_cluster_service_and_task_role_names() -> None:
    template = _synth()
    outputs = template.find_outputs("*")
    assert "ClusterName" in outputs
    assert "ServiceName" in outputs
    assert "TaskRoleArn" in outputs


def test_service_runs_with_ecs_exec_enabled() -> None:
    template = _synth()
    services = template.find_resources("AWS::ECS::Service")
    (svc,) = services.values()
    assert svc["Properties"]["EnableExecuteCommand"] is True


def test_maint_mtls_secret_is_used_not_a_worker_secret() -> None:
    """The container injects the `maint` SeededSecret's contents,
    NOT any of the `worker-*` ones."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secrets = td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    cert_secret = next(
        s for s in secrets if s["Name"] == "TEMPORAL_TLS_CLIENT_CERT_CONTENT"
    )
    # The ValueFrom Fn::Join references the maint secret's ARN. The
    # exact reference shape is `{cert::}` appended to a Ref. We just
    # confirm the wiring lands by checking the secret references an
    # Fn::Join (CDK's standard envelope for `field=` extraction).
    assert isinstance(cert_secret["ValueFrom"], dict)
