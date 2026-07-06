# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.gha_oidc_role import GitHubRepoIdentity
from cert_ra_infra.stacks._config import load_env
from cert_ra_infra.stacks.data import DataStack, DataStackProps
from cert_ra_infra.stacks.identity import IdentityStack, IdentityStackProps
from cert_ra_infra.stacks.network import NetworkStack
from cert_ra_infra.stacks.observability import (
    ObservabilityStack,
    ObservabilityStackProps,
)
from cert_ra_infra.stacks.secrets import SecretsStack, SecretsStackProps
from cert_ra_infra.stacks.temporal import TemporalStack, TemporalStackProps
from cert_ra_infra.stacks.workers import WorkersStack, WorkersStackProps

_INSTALLER_ARN = (
    "arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/"
    "AWSReservedSSO_CertRaInstaller_*"
)


def _synth() -> assertions.Template:
    app = cdk.App()
    cfg = load_env("staging")
    env = cdk.Environment(account="111111111111", region=cfg.region)
    identity = IdentityStack(
        app,
        f"CertRa-IdentityStack-{cfg.env}",
        env=env,
        env_config=cfg,
        identity_props=IdentityStackProps(
            github_repo=GitHubRepoIdentity(
                owner="Certora", repo="risk-assessment"
            ),
            installer_role_arn_pattern=_INSTALLER_ARN,
        ),
    )
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
    stack = WorkersStack(
        app,
        f"CertRa-WorkersStack-{cfg.env}",
        env=env,
        env_config=cfg,
        workers_props=WorkersStackProps(
            network=network,
            data=data,
            secrets=secrets,
            observability=observability,
            identity=identity,
            temporal=temporal,
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_two_worker_services_and_one_cluster() -> None:
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 2)
    template.resource_count_is("AWS::ECS::Cluster", 1)


def test_worker_services_have_pinned_names() -> None:
    template = _synth()
    services = template.find_resources("AWS::ECS::Service")
    names = {svc["Properties"]["ServiceName"] for svc in services.values()}
    assert names == {
        "cert-ra-worker-metrics-staging",
        "cert-ra-worker-alerts-staging",
    }


def test_exports_per_worker_service_names_and_cluster_arn() -> None:
    template = _synth()
    outputs = template.find_outputs("*")
    assert "ClusterArn" in outputs
    assert "MetricsWorkerServiceName" in outputs
    assert "AlertsWorkerServiceName" in outputs


def test_both_workers_inject_their_own_mtls_cert() -> None:
    """Each worker has TEMPORAL_TLS_CLIENT_CERT_CONTENT mounted, but
    from its own SeededSecret — they must NOT share a cert."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) == 2
    for td in task_defs.values():
        secret_names = {
            s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
        }
        assert {
            "TEMPORAL_TLS_CLIENT_CERT_CONTENT",
            "TEMPORAL_TLS_CLIENT_KEY_CONTENT",
            "TEMPORAL_TLS_CA_CERT_CONTENT",
        }.issubset(secret_names)


def test_both_workers_inject_rpc_providers_and_sentry() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    for td in task_defs.values():
        secret_names = {
            s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
        }
        assert "RPC_PROVIDERS" in secret_names
        assert "SENTRY_DSN" in secret_names
        assert "ANTHROPIC_API_KEY" in secret_names
        assert "THE_GRAPH_API_KEY" in secret_names


def test_task_queue_env_var_differs_per_worker() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    task_queues: set[str] = set()
    for td in task_defs.values():
        env_vars = {
            e["Name"]: e["Value"]
            for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
        }
        task_queues.add(env_vars["TASK_QUEUE"])
    assert task_queues == {"metrics", "alerts"}


def test_canton_scan_urls_only_on_metrics_worker() -> None:
    """The Canton collector runs on the metrics queue, so only that worker
    gets CERT_RA_CANTON_SCAN_URLS (a public, non-secret env var)."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    by_queue: dict[str, dict[str, str]] = {}
    for td in task_defs.values():
        env_vars = {
            e["Name"]: e["Value"]
            for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
        }
        by_queue[env_vars["TASK_QUEUE"]] = env_vars

    metrics_env = by_queue["metrics"]
    assert "CERT_RA_CANTON_SCAN_URLS" in metrics_env
    assert "api.cantonnodes.com" in metrics_env["CERT_RA_CANTON_SCAN_URLS"]
    assert "CERT_RA_CANTON_SCAN_URLS" not in by_queue["alerts"]
