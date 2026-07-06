# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs

from cert_ra_infra.constructs.workers.worker_service import (
    WorkerSecretInjection,
    WorkerService,
    WorkerServiceProps,
)

_ECR_REPO_ARN = "arn:aws:ecr:us-east-1:111111111111:repository/cert-ra"
_ECR_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
)
_SECRETS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
)
_RDS_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/data/rds/master-AbCdEf"
)
_RDS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/11111111-2222-3333-4444-555555555555"
)
_LOGS_CMK_ARN = (
    "arn:aws:kms:us-east-1:111111111111:key/66666666-7777-8888-9999-aaaaaaaaaaaa"
)
_MTLS_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/temporal/mtls/worker-metrics-AbCdEf"
)
_RPC_SECRET_ARN = (
    "arn:aws:secretsmanager:us-east-1:111111111111:"
    "secret:/cert-ra/staging/rpc/providers-AbCdEf"
)
_TEMPORAL_ENDPOINT = (
    "cert-ra-temporal-staging-internal-nlb-abc.elb.us-east-1.amazonaws.com"
)


def _synth(
    *,
    secret_injections: list[WorkerSecretInjection] | None = None,
    queue: str = "metrics",
) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    vpc = ec2.Vpc(stack, "Vpc", max_azs=2, nat_gateways=0)
    cluster = ecs.Cluster(stack, "Cluster", vpc=vpc)
    worker_sg = ec2.SecurityGroup(stack, "WorkerSg", vpc=vpc, allow_all_outbound=True)
    WorkerService(
        stack,
        "Worker",
        props=WorkerServiceProps(
            service_name=f"cert-ra-worker-{queue}-staging",
            cluster=cluster,
            vpc=vpc,
            private_subnets=list(vpc.private_subnets),
            worker_security_group=worker_sg,
            ecr_repo_arn=_ECR_REPO_ARN,
            ecr_repo_name="cert-ra",
            ecr_cmk_arn=_ECR_CMK_ARN,
            image_tag="latest",
            command=[f"certora-risk-{queue}-worker"],
            rds_master_secret_arn=_RDS_SECRET_ARN,
            rds_master_secret_cmk_arn=_RDS_CMK_ARN,
            rds_endpoint="cert-ra.cluster-abc.us-east-1.rds.amazonaws.com",
            rds_port="5432",
            secrets_cmk_arn=_SECRETS_CMK_ARN,
            secret_injections=secret_injections or [],
            worker_mtls_secret_arn=_MTLS_SECRET_ARN,
            temporal_frontend_endpoint=_TEMPORAL_ENDPOINT,
            temporal_tls_server_name="temporal-frontend.cert-ra.local",
            logs_cmk_arn=_LOGS_CMK_ARN,
            extra_env={"TASK_QUEUE": queue},
        ),
    )
    return assertions.Template.from_stack(stack)


def test_creates_single_fargate_service() -> None:
    template = _synth()
    template.resource_count_is("AWS::ECS::Service", 1)


def test_uses_default_rolling_deployment_controller_not_codedeploy() -> None:
    """Workers use the ECS rolling controller (not CodeDeploy) per the
    design spec — they have no public listener to traffic-shift."""
    template = _synth()
    services = template.find_resources("AWS::ECS::Service")
    (svc,) = services.values()
    # When deployment_controller is not set, CFN omits the field entirely
    # (default = ECS rolling). CodeDeploy would surface as `{"Type":
    # "CODE_DEPLOY"}` in the template.
    controller = svc["Properties"].get("DeploymentController")
    assert controller is None or controller.get("Type") in (None, "ECS")


def test_deployment_circuit_breaker_enabled_with_rollback() -> None:
    template = _synth()
    services = template.find_resources("AWS::ECS::Service")
    (svc,) = services.values()
    config = svc["Properties"]["DeploymentConfiguration"]["DeploymentCircuitBreaker"]
    assert config["Enable"] is True
    assert config["Rollback"] is True


def test_container_command_pins_the_worker_entrypoint() -> None:
    template = _synth(queue="alerts")
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    command = td["Properties"]["ContainerDefinitions"][0]["Command"]
    assert command == ["certora-risk-alerts-worker"]


def test_mtls_cert_key_chain_mounted_as_ecs_secrets() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {
        "TEMPORAL_TLS_CLIENT_CERT_CONTENT",
        "TEMPORAL_TLS_CLIENT_KEY_CONTENT",
        "TEMPORAL_TLS_CA_CERT_CONTENT",
    }.issubset(secret_names)


def test_database_credentials_injected_as_ecs_secrets() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert {"DATABASE_USER", "DATABASE_PASSWORD"}.issubset(secret_names)


def test_temporal_endpoint_and_sni_in_plain_env() -> None:
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    env_vars = {
        e["Name"]: e["Value"]
        for e in td["Properties"]["ContainerDefinitions"][0]["Environment"]
    }
    assert env_vars["TEMPORAL_ADDRESS"] == _TEMPORAL_ENDPOINT
    assert env_vars["TEMPORAL_TLS_SERVER_NAME"] == "temporal-frontend.cert-ra.local"
    assert env_vars["TASK_QUEUE"] == "metrics"


def test_extra_secret_injections_become_ecs_secrets() -> None:
    template = _synth(
        secret_injections=[
            WorkerSecretInjection(env_var="RPC_PROVIDERS", secret_arn=_RPC_SECRET_ARN),
        ]
    )
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    secret_names = {
        s["Name"] for s in td["Properties"]["ContainerDefinitions"][0]["Secrets"]
    }
    assert "RPC_PROVIDERS" in secret_names


def test_no_port_mappings_when_container_port_unset() -> None:
    """Workers don't accept inbound; the task def should have empty
    port mappings."""
    template = _synth()
    task_defs = template.find_resources("AWS::ECS::TaskDefinition")
    (td,) = task_defs.values()
    port_mappings = td["Properties"]["ContainerDefinitions"][0].get("PortMappings", [])
    assert port_mappings == []


def test_service_does_not_assign_public_ip() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECS::Service",
        {
            "NetworkConfiguration": {
                "AwsvpcConfiguration": {"AssignPublicIp": "DISABLED"}
            }
        },
    )


def test_log_group_is_encrypted_with_provided_cmk() -> None:
    template = _synth()
    log_groups = template.find_resources("AWS::Logs::LogGroup")
    assert len(log_groups) == 1
    (lg,) = log_groups.values()
    assert lg["Properties"]["KmsKeyId"] == _LOGS_CMK_ARN
    assert lg["Properties"]["LogGroupName"] == "/ecs/cert-ra-worker-metrics-staging"
