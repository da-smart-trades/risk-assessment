# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.constructs.identity.ecr_repo import (
    CertRaEcrRepo,
    CertRaEcrRepoProps,
)


def _synth(**kwargs: object) -> assertions.Template:
    app = cdk.App()
    stack = cdk.Stack(
        app,
        "TestStack",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    kwargs.setdefault("env", "test")
    CertRaEcrRepo(stack, "Ecr", props=CertRaEcrRepoProps(**kwargs))  # type: ignore[arg-type]
    return assertions.Template.from_stack(stack)


def test_repo_name_includes_env_suffix() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECR::Repository", {"RepositoryName": "cert-ra-test"}
    )


def test_tag_mutability_is_immutable() -> None:
    """A9 fix: tags cannot be reassigned to a different image."""
    template = _synth()
    template.has_resource_properties(
        "AWS::ECR::Repository", {"ImageTagMutability": "IMMUTABLE"}
    )


def test_scan_on_push_is_enabled() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::ECR::Repository",
        {"ImageScanningConfiguration": {"ScanOnPush": True}},
    )


def test_encryption_uses_kms_cmk_from_construct() -> None:
    template = _synth()
    repos = template.find_resources("AWS::ECR::Repository")
    (repo_props,) = (r["Properties"] for r in repos.values())
    enc = repo_props["EncryptionConfiguration"]
    assert enc["EncryptionType"] == "KMS"
    assert "Fn::GetAtt" in enc["KmsKey"]  # references the CMK in the same stack


def test_encryption_cmk_alias_is_cert_ra_ecr() -> None:
    template = _synth()
    template.has_resource_properties(
        "AWS::KMS::Alias", {"AliasName": "alias/cert-ra-ecr-test"}
    )


def test_lifecycle_policy_has_untagged_and_tagged_rules() -> None:
    template = _synth(untagged_image_age_days=7, tagged_image_age_days=90)
    repos = template.find_resources("AWS::ECR::Repository")
    (repo_props,) = (r["Properties"] for r in repos.values())
    import json

    policy = json.loads(repo_props["LifecyclePolicy"]["LifecyclePolicyText"])
    rules = policy["rules"]
    assert len(rules) == 2
    untagged = next(r for r in rules if r["selection"]["tagStatus"] == "untagged")
    assert untagged["selection"]["countNumber"] == 7
    tagged = next(r for r in rules if r["selection"]["tagStatus"] == "tagged")
    assert tagged["selection"]["countNumber"] == 90
    assert tagged["selection"]["tagPatternList"] == ["sha-*"]


def test_lifecycle_ages_are_configurable() -> None:
    import json

    template = _synth(untagged_image_age_days=3, tagged_image_age_days=30)
    repos = template.find_resources("AWS::ECR::Repository")
    (repo_props,) = (r["Properties"] for r in repos.values())
    policy = json.loads(repo_props["LifecyclePolicy"]["LifecyclePolicyText"])
    rules = policy["rules"]
    untagged = next(r for r in rules if r["selection"]["tagStatus"] == "untagged")
    assert untagged["selection"]["countNumber"] == 3
    tagged = next(r for r in rules if r["selection"]["tagStatus"] == "tagged")
    assert tagged["selection"]["countNumber"] == 30


def test_repo_uses_retain_removal_policy() -> None:
    template = _synth()
    repos = template.find_resources("AWS::ECR::Repository")
    (repo,) = repos.values()
    assert repo.get("DeletionPolicy") == "Retain"
