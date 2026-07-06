# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass, field

import aws_cdk as cdk
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from cert_ra_infra.constructs.data.cmk import NarrowKmsCmk, NarrowKmsCmkProps


def _empty_str_list() -> list[str]:
    return []


@dataclass(frozen=True, slots=True)
class CertRaEcrRepoProps:
    """Props for CertRaEcrRepo.

    See § Container image baselines (B4) + A9 (tag immutability) in the
    design spec.
    """

    env: str
    """Deployment env (`staging` or `prod`). Per-env split: build.yml pushes
    the same digest to both `cert-ra-staging` and `cert-ra-prod` repos so each
    env's deploy reads from its own."""

    admin_role_arns: list[str] = field(default_factory=_empty_str_list)
    """Roles allowed to administer the ECR encryption CMK (typically Installer)."""

    untagged_image_age_days: int = 7
    """Lifecycle: delete untagged images older than N days."""

    tagged_image_age_days: int = 90
    """Lifecycle: delete tagged images older than N days unless `preserve=true`."""

    @property
    def repo_name(self) -> str:
        return f"cert-ra-{self.env}"


class CertRaEcrRepo(Construct):
    """The single `cert-ra` ECR repository.

    Tag mutability: IMMUTABLE (A9 — a tag, once pushed, cannot be reassigned).
    Lifecycle: untagged → 7 days, tagged → 90 days for `sha-*` tags.
    Scan-on-push: enabled.
    Encryption: KMS CMK (`cert-ra-ecr-cmk`) per the M2 resource ownership matrix.
    """

    repository: ecr.Repository
    encryption_cmk: NarrowKmsCmk

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        props: CertRaEcrRepoProps,
    ) -> None:
        super().__init__(scope, construct_id)

        self.encryption_cmk = NarrowKmsCmk(
            self,
            "EncryptionCmk",
            props=NarrowKmsCmkProps(
                key_id="ecr",
                env=props.env,
                purpose="encrypt",
                service_principals=["ecr.amazonaws.com"],
                admin_roles=list(props.admin_role_arns),
            ),
        )

        self.repository = ecr.Repository(
            self,
            "Repo",
            repository_name=props.repo_name,
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            encryption=ecr.RepositoryEncryption.KMS,
            encryption_key=self.encryption_cmk.key,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    rule_priority=1,
                    description="Delete untagged images after N days",
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=cdk.Duration.days(props.untagged_image_age_days),
                ),
                ecr.LifecycleRule(
                    rule_priority=2,
                    description="Delete tagged images (sha-*) after N days",
                    tag_status=ecr.TagStatus.TAGGED,
                    tag_pattern_list=["sha-*"],
                    max_image_age=cdk.Duration.days(props.tagged_image_age_days),
                ),
            ],
        )

    @property
    def repository_arn(self) -> str:
        return self.repository.repository_arn

    @property
    def encryption_cmk_arn(self) -> str:
        return self.encryption_cmk.key.key_arn
