#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Seed Secrets Manager entries created (empty) by SecretsStack.

Walks the named secrets that SecretsStack provisions as empty shells,
prompts the operator for real values, and writes them via
`secretsmanager:PutSecretValue`. Refuses to overwrite secrets that
already have a non-placeholder value unless `--force` is passed.

Skips the Temporal mTLS shells — those are auto-populated by
TemporalStack's `InitialCertIssuance` Custom Resource during stack
create (and refreshed daily by `CertRenewal`). Trying to overwrite
them by hand would clobber valid certs.

Usage:
    python3 seed-secrets.py --env staging
    python3 seed-secrets.py --env prod --force
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

import boto3

# The placeholder marker SeededSecret writes when CDK creates the
# shell. `seed-secrets.py` treats any value containing this marker as
# "still empty" — and refuses to overwrite any value that doesn't
# contain it, unless --force.
PLACEHOLDER = "__SEED_ME__"


@dataclass(frozen=True, slots=True)
class SecretSeed:
    """One entry in the seed list.

    `name_template` is a Python format string with `{env}` substituted
    at runtime. `fields` is the JSON keys to prompt for (dot-paths
    create nested dicts). Empty `fields` means the value is opaque
    and the operator pastes a single string.
    """

    name_template: str
    fields: list[str]
    is_json: bool
    description: str


# Mirrors the SeededSecret entries in
# `infra/cert_ra_infra/stacks/secrets.py`. Keep in sync — adding a
# new secret to SecretsStack means adding a row here. mTLS secrets
# are intentionally absent: see module docstring.
SECRETS: list[SecretSeed] = [
    SecretSeed(
        name_template="/cert-ra/{env}/oauth/providers",
        fields=[
            "google.client_id",
            "google.client_secret",
            "github.client_id",
            "github.client_secret",
            "microsoft.client_id",
            "microsoft.client_secret",
        ],
        is_json=True,
        description="OAuth client IDs + secrets for Google / GitHub / Microsoft",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/rpc/providers",
        fields=[
            "ethereum_private_rpc_1",
            "ethereum_private_rpc_2",
            "arbitrum_private_rpc_1",
            "base_private_rpc_1",
            "polygon_private_rpc_1",
            "solana_private_rpc_1",
            "avalanche_c_private_rpc_1",
            "optimism_private_rpc_1",
        ],
        is_json=True,
        description="Private RPC URLs for each chain (each field → CERT_RA_RPC_<FIELD>)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/app/session-secret",
        fields=[],
        is_json=False,
        description="Litestar session signing key (32+ random bytes, base64)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/email/resend-api-key",
        fields=[],
        is_json=False,
        description="Resend API key for transactional email",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/sentry/dsn",
        fields=[],
        is_json=False,
        description="Sentry DSN (full URL including project ID)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/anthropic/api-key",
        fields=[],
        is_json=False,
        description="Anthropic LLM API key (ANTHROPIC_API_KEY)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/openai/api-key",
        fields=[],
        is_json=False,
        description="OpenAI API key (OPENAI_API_KEY)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/the-graph/api-key",
        fields=[],
        is_json=False,
        description="The Graph external API key (THE_GRAPH_API_KEY)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/dune/api-key",
        fields=[],
        is_json=False,
        description="Dune Analytics API key (CERT_RA_DUNE_API_KEY)",
    ),
    SecretSeed(
        name_template="/cert-ra/{env}/app/superuser",
        fields=["email", "password"],
        is_json=True,
        description="Bootstrap superuser credentials (email + password). Created once on first app startup; ignored thereafter.",
    ),
]


class SecretsClient:
    """Thin wrapper around boto3's Secrets Manager client.

    Lets the rest of the script stay testable without binding to boto3
    types directly. Captures only the two operations seed-secrets.py
    needs.
    """

    def __init__(self) -> None:
        self._client = boto3.client("secretsmanager")
        self._not_found = self._client.exceptions.ResourceNotFoundException

    def get_value(self, name: str) -> str | None:
        try:
            return self._client.get_secret_value(SecretId=name)["SecretString"]
        except self._not_found:
            return None

    def put_value(self, name: str, value: str) -> None:
        self._client.put_secret_value(SecretId=name, SecretString=value)


def seed_secret(
    client: SecretsClient,
    seed: SecretSeed,
    env: str,
    force: bool,
) -> None:
    """Prompt + write one secret, respecting placeholder + --force rules."""
    name = seed.name_template.format(env=env)

    current = client.get_value(name)
    if current is None:
        print(f"  SKIP: {name} not found (SecretsStack not deployed yet?)")
        return

    if current and PLACEHOLDER not in current and not force:
        print(f"  SKIP: {name} already has a real value (use --force to overwrite)")
        return

    print(f"  {name}")
    print(f"    {seed.description}")
    # Values are echoed (plain input, not getpass) so the operator can
    # visually verify what they pasted, and stripped of leading/trailing
    # whitespace so a stray newline/space from a copy-paste doesn't end up
    # in the stored secret.
    if seed.is_json:
        # Store each field as a flat top-level key (e.g. "google.client_id").
        # ECS secret field extraction (`valueFrom ARN:json-key`) does a flat
        # key lookup — it does NOT navigate nested JSON. Nesting the values
        # ({"google": {"client_id": ...}}) would make ECS silently return an
        # empty string for every injected env var.
        new_value: dict[str, str] = {}
        for path in seed.fields:
            new_value[path] = input(f"    {path} = ").strip()
        payload = json.dumps(new_value)
    else:
        payload = input("    value = ").strip()

    client.put_value(name, payload)
    print(f"    OK: wrote {name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively seed cert-ra Secrets Manager entries. "
            "Refuses to overwrite real values without --force."
        ),
    )
    parser.add_argument("--env", required=True, choices=["staging", "prod"])
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite secrets that already have real (non-placeholder) values.",
    )
    args = parser.parse_args()

    client = SecretsClient()
    print(f"Seeding cert-ra secrets for env={args.env} (force={args.force})")
    print()
    for seed in SECRETS:
        seed_secret(client, seed, args.env, args.force)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
