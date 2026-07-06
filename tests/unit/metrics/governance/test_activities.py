# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for the governance activity dispatch and per-chain fetcher wiring."""

# ruff: noqa: EM102, TRY003 — f-string AssertionError is fine in mock handlers.

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest

from cert_ra.metrics.governance import activities
from cert_ra.metrics.governance.schemas import GovernanceParams

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


def _install_transport(mocker: MockerFixture, handler: httpx.MockTransport) -> None:
    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(activities.httpx, "AsyncClient", side_effect=factory)


async def test_fetch_governance_rejects_unsupported_pair() -> None:
    with pytest.raises(ValueError, match="not supported"):
        await activities.fetch_governance(
            GovernanceParams(chain="ETHEREUM", event_type="execution")
        )


# ---------------------------------------------------------------------------
# Ethereum: confirmed_eips — meta-EIP parsing
# ---------------------------------------------------------------------------


async def test_fetch_eth_confirmed_eips_counts_distinct_refs_excluding_self(
    mocker: MockerFixture,
) -> None:
    meta_eip = activities._ETH_NEXT_META_EIP  # noqa: SLF001
    body = f"""---
eip: {meta_eip}
title: Sample Meta
status: Draft
---

## Included EIPs

- EIP-1234: First feature
- EIP-5678: Second feature
- EIP-9999: Third feature

Cross-references: see EIP-1234 again and EIP-{meta_eip} (this meta-EIP itself).
"""

    def handler(request: httpx.Request) -> httpx.Response:
        expected = activities._ETH_EIP_RAW_URL.format(eip=meta_eip)  # noqa: SLF001
        if str(request.url) != expected:
            raise AssertionError(f"unexpected url: {request.url}")
        return httpx.Response(200, text=body)

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_governance(
        GovernanceParams(chain="ETHEREUM", event_type="confirmed_eips")
    )

    # Three distinct EIP refs (1234, 5678, 9999) — duplicates dedupe, self excluded.
    assert result.chain == "ETHEREUM"
    assert result.event_type == "confirmed_eips"
    assert result.count == 3


# ---------------------------------------------------------------------------
# Ethereum: last_call_eips — repo scan + frontmatter parsing
# ---------------------------------------------------------------------------


async def test_fetch_eth_last_call_eips_counts_last_call_status(
    mocker: MockerFixture,
) -> None:
    tree_payload = {
        "tree": [
            {"path": "EIPS/eip-1.md", "type": "blob"},
            {"path": "EIPS/eip-2.md", "type": "blob"},
            {"path": "EIPS/eip-3.md", "type": "blob"},
            {"path": "EIPS/eip-4.md", "type": "blob"},
            {"path": "README.md", "type": "blob"},  # ignored
            {"path": "EIPS/_template.md", "type": "blob"},  # ignored (no number)
        ]
    }
    eip_bodies = {
        1: "---\nstatus: Last Call\n---\nbody",
        2: "---\nstatus: Final\n---\nbody",
        3: "---\nstatus: last call\n---\nbody",  # case-insensitive
        4: "---\nstatus: Draft\n---\nbody",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == activities._ETH_EIPS_TREE_URL:  # noqa: SLF001
            return httpx.Response(200, text=json.dumps(tree_payload))
        for eip, body in eip_bodies.items():
            if url == activities._ETH_EIP_RAW_URL.format(eip=eip):  # noqa: SLF001
                return httpx.Response(200, text=body)
        raise AssertionError(f"unexpected url: {url}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_governance(
        GovernanceParams(chain="ETHEREUM", event_type="last_call_eips")
    )

    # EIPs 1 and 3 are Last Call (one cased differently to verify normalization).
    assert result.count == 2


async def test_fetch_eth_last_call_eips_skips_files_that_fail_to_fetch(
    mocker: MockerFixture,
) -> None:
    tree_payload = {
        "tree": [
            {"path": "EIPS/eip-1.md"},
            {"path": "EIPS/eip-2.md"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == activities._ETH_EIPS_TREE_URL:  # noqa: SLF001
            return httpx.Response(200, text=json.dumps(tree_payload))
        if url == activities._ETH_EIP_RAW_URL.format(eip=1):  # noqa: SLF001
            return httpx.Response(200, text="---\nstatus: Last Call\n---\n")
        # eip-2 unavailable
        return httpx.Response(500, text="oops")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_governance(
        GovernanceParams(chain="ETHEREUM", event_type="last_call_eips")
    )

    # Failed fetch is skipped, so only eip-1 contributes.
    assert result.count == 1


def test_parse_frontmatter_status_extracts_value() -> None:
    md = "---\ntitle: foo\nstatus: Last Call\nauthor: x\n---\n\nbody"
    assert activities._parse_frontmatter_status(md) == "Last Call"  # noqa: SLF001


def test_parse_frontmatter_status_returns_none_when_missing() -> None:
    md = "---\ntitle: foo\nauthor: x\n---\nbody"
    assert activities._parse_frontmatter_status(md) is None  # noqa: SLF001


def test_parse_frontmatter_status_returns_none_when_no_frontmatter() -> None:
    assert activities._parse_frontmatter_status("# just a heading") is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Arbitrum / Base — unchanged from prior coverage
# ---------------------------------------------------------------------------


async def test_fetch_governance_arb_execution_uses_timelock_topics(
    mocker: MockerFixture,
) -> None:
    captured: dict[str, object] = {}

    async def fake_count_evm_events(**kwargs: object) -> int:
        captured.update(kwargs)
        return 4

    mocker.patch.object(
        activities,
        "get_rpc_settings",
        return_value=SimpleNamespace(
            arbitrum_urls=["https://arb.example"],
            base_urls=["https://base.example"],
        ),
    )
    mocker.patch.object(
        activities, "count_evm_events", side_effect=fake_count_evm_events
    )

    result = await activities.fetch_governance(
        GovernanceParams(chain="ARBITRUM", event_type="execution")
    )

    assert result.count == 4
    assert captured["address"] == activities._ARB_TIMELOCK  # noqa: SLF001
    assert captured["topics"] == activities._ARB_TIMELOCK_TOPICS  # noqa: SLF001
    assert captured["urls"] == ["https://arb.example"]
    assert captured["label"] == "arb_timelock"


async def test_fetch_governance_arb_emergency_uses_upgrade_executor_all_events(
    mocker: MockerFixture,
) -> None:
    captured: dict[str, object] = {}

    async def fake_count_evm_events(**kwargs: object) -> int:
        captured.update(kwargs)
        return 1

    mocker.patch.object(
        activities,
        "get_rpc_settings",
        return_value=SimpleNamespace(
            arbitrum_urls=["https://arb.example"],
            base_urls=[],
        ),
    )
    mocker.patch.object(
        activities, "count_evm_events", side_effect=fake_count_evm_events
    )

    await activities.fetch_governance(
        GovernanceParams(chain="ARBITRUM", event_type="emergency")
    )

    assert captured["address"] == activities._ARB_UPGRADE_EXECUTOR  # noqa: SLF001
    # ``topics=None`` means "all events from this contract".
    assert captured["topics"] is None
    assert captured["label"] == "arb_upgrade_executor"


async def test_fetch_governance_base_execution_uses_base_urls(
    mocker: MockerFixture,
) -> None:
    captured: dict[str, object] = {}

    async def fake_count_evm_events(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    mocker.patch.object(
        activities,
        "get_rpc_settings",
        return_value=SimpleNamespace(
            arbitrum_urls=[],
            base_urls=["https://base.example"],
        ),
    )
    mocker.patch.object(
        activities, "count_evm_events", side_effect=fake_count_evm_events
    )

    await activities.fetch_governance(
        GovernanceParams(chain="BASE", event_type="execution")
    )

    assert captured["address"] == activities._BASE_UPGRADE_EXECUTOR  # noqa: SLF001
    assert captured["urls"] == ["https://base.example"]
    assert captured["lookback_blocks"] == activities._BASE_LOOKBACK_BLOCKS  # noqa: SLF001
