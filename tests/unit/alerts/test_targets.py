# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the polymorphic target_config validator."""

from __future__ import annotations

from uuid import uuid4

import msgspec
import pytest

from cert_ra.api.domain.alerts.targets import (
    MarketAnchorTargetConfig,
    MarketControlTargetConfig,
    MarketPdTargetConfig,
    MetricTargetConfig,
    dump_target_config,
    parse_target_config,
)
from cert_ra.types import AlertTargetKind


def test_parse_metric_round_trips() -> None:
    raw = {"type": "METRIC", "metricType": "GAS_PRICE", "chain": "ETHEREUM", "token": None}
    parsed = parse_target_config(AlertTargetKind.METRIC, raw)
    assert isinstance(parsed, MetricTargetConfig)
    assert parsed.metric_type.value == "GAS_PRICE"
    assert parsed.chain is not None
    assert parsed.chain.value == "ETHEREUM"
    assert parsed.token is None
    dumped = dump_target_config(AlertTargetKind.METRIC, parsed)
    assert dumped["metricType"] == "GAS_PRICE"
    assert dumped["chain"] == "ETHEREUM"


def test_parse_market_pd_round_trips() -> None:
    market_id = uuid4()
    raw = {
        "type": "MARKET_PD",
        "marketConfigId": str(market_id),
        "chainId": 1,
        "marketIdHex": "0xdeadbeef",
    }
    parsed = parse_target_config(AlertTargetKind.MARKET_PD, raw)
    assert isinstance(parsed, MarketPdTargetConfig)
    assert parsed.market_config_id == market_id
    assert parsed.chain_id == 1
    assert parsed.market_id_hex == "0xdeadbeef"


def test_parse_market_anchor_carries_sub_category() -> None:
    raw = {
        "type": "MARKET_ANCHOR",
        "marketConfigId": str(uuid4()),
        "chainId": 1,
        "marketIdHex": "0xabc",
        "subCategory": "liquidity_risk",
    }
    parsed = parse_target_config(AlertTargetKind.MARKET_ANCHOR, raw)
    assert isinstance(parsed, MarketAnchorTargetConfig)
    assert parsed.sub_category == "liquidity_risk"


def test_parse_market_control_carries_sub_category() -> None:
    raw = {
        "type": "MARKET_CONTROL",
        "marketConfigId": str(uuid4()),
        "chainId": 1,
        "marketIdHex": "0xabc",
        "subCategory": "oracle_health",
    }
    parsed = parse_target_config(AlertTargetKind.MARKET_CONTROL, raw)
    assert isinstance(parsed, MarketControlTargetConfig)
    assert parsed.sub_category == "oracle_health"


def test_parse_rejects_malformed_payload() -> None:
    raw = {"type": "MARKET_PD", "marketConfigId": "not-a-uuid", "chainId": 1, "marketIdHex": "0x0"}
    with pytest.raises(msgspec.ValidationError):
        parse_target_config(AlertTargetKind.MARKET_PD, raw)


def test_dump_validates_raw_dict_against_kind() -> None:
    raw = {
        "type": "MARKET_ANCHOR",
        "marketConfigId": str(uuid4()),
        "chainId": 1,
        "marketIdHex": "0xabc",
        "subCategory": "k",
    }
    dumped = dump_target_config(AlertTargetKind.MARKET_ANCHOR, raw)
    assert dumped["subCategory"] == "k"
    assert dumped["chainId"] == 1
