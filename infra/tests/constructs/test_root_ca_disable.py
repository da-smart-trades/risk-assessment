# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.temporal.root_ca_disable import (
    RootCaDisable,
    RootCaDisableProps,
)

_ROOT_CA_ARN = (
    "arn:aws:acm-pca:us-east-1:111111111111:"
    "certificate-authority/cccccccc-dddd-eeee-ffff-000000000000"
)


def _synth() -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    RootCaDisable(
        stack,
        "RootCaDisable",
        props=RootCaDisableProps(root_ca_arn=_ROOT_CA_ARN),
    )
    return assertions.Template.from_stack(stack)


def test_handler_lambda_is_python_312() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {"Runtime": "python3.12", "Handler": "handler.handler"},
    )


def test_handler_has_update_ca_permission_scoped_to_root_ca() -> None:
    template = _synth()
    policies = template.find_resources("AWS::IAM::Policy")
    matching: list[dict[str, object]] = []
    for policy in policies.values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") == "DisableRootCa":
                matching.append(stmt)
    assert len(matching) == 1
    stmt = matching[0]
    actions = stmt["Action"]
    action_set: set[object] = (
        set(actions)  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(actions, list)
        else {actions}
    )
    assert action_set == {"acm-pca:UpdateCertificateAuthority"}
    assert stmt["Resource"] == _ROOT_CA_ARN


def test_custom_resource_carries_root_ca_arn_property() -> None:
    template = _synth()
    custom_resources = template.find_resources("Custom::TemporalRootCaDisable")
    assert len(custom_resources) == 1
    (cr,) = custom_resources.values()
    assert cr["Properties"]["RootCaArn"] == _ROOT_CA_ARN


def test_handler_has_5_minute_timeout() -> None:
    template = _synth()
    # The Provider framework also creates a Lambda; filter to ours by
    # the Handler entrypoint.
    functions = template.find_resources(
        "AWS::Lambda::Function", {"Properties": {"Handler": "handler.handler"}}
    )
    assert len(functions) == 1
    (fn,) = functions.values()
    assert fn["Properties"]["Timeout"] == 300


def test_handler_has_128mb_memory() -> None:
    template = _synth()
    functions = template.find_resources(
        "AWS::Lambda::Function", {"Properties": {"Handler": "handler.handler"}}
    )
    (fn,) = functions.values()
    assert fn["Properties"]["MemorySize"] == 128
