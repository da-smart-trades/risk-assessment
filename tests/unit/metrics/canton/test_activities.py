# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for Canton metric fetch activities + pure parsing helpers (HTTP mocked)."""

# ruff: noqa: EM102, TRY003 — f-string AssertionError is fine in mock handlers.
# ruff: noqa: SLF001 — this module exercises the collector's internal helpers.
# ruff: noqa: D205, FBT003 — test docstrings / literal bool assertions are fine.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from cert_ra.metrics.canton import activities
from cert_ra.metrics.canton.scan_client import CantonScanClient
from cert_ra.metrics.throughput import canton as throughput_canton
from cert_ra.settings.canton import CantonSettings

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Pure parsing helpers
# ---------------------------------------------------------------------------


def test_coerce_int_handles_decimal_strings_and_floats() -> None:
    assert activities._coerce_int("42") == 42
    assert activities._coerce_int("42.0") == 42
    assert activities._coerce_int(7) == 7
    assert activities._coerce_int(3.9) == 3
    assert activities._coerce_int(True) is None  # bools rejected
    assert activities._coerce_int("not-a-number") is None
    assert activities._coerce_int(None) is None


def test_coerce_float_handles_strings() -> None:
    assert activities._coerce_float("0.005") == pytest.approx(0.005)
    assert activities._coerce_float(2) == pytest.approx(2.0)
    assert activities._coerce_float("bad") is None
    assert activities._coerce_float(None) is None


def test_parse_iso_and_seconds_since() -> None:
    assert activities._parse_iso("not-a-date") is None
    assert activities._parse_iso(None) is None
    past = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
    seconds = activities._seconds_since(past)
    assert seconds is not None
    assert 100 < seconds < 200  # ~120s, allowing scheduling slack


def test_round_number_supports_scalar_and_nested() -> None:
    assert activities._round_number({"round": 5}) == 5
    assert activities._round_number({"round": {"number": "9"}}) == 9
    assert activities._round_number({}) is None


def test_latest_open_round_picks_highest_number() -> None:
    resp = {
        "open_mining_rounds": [
            {"contract": {"payload": {"round": {"number": "3"}, "opensAt": "x"}}},
            {"contract": {"payload": {"round": {"number": "5"}, "opensAt": "y"}}},
        ]
    }
    # Unparseable opensAt → fall back to the highest-numbered round.
    payload, count = activities._latest_open_round(resp)
    assert count == 2
    assert activities._round_number(payload) == 5


def test_latest_open_round_picks_currently_open_not_future() -> None:
    """The highest-numbered round may not have opened yet; pick the one whose
    opensAt is most recently in the past so round_advance stays positive.
    """
    past = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
    future = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()
    resp = {
        "open_mining_rounds": {
            "cidA": {
                "contract": {"payload": {"round": {"number": "10"}, "opensAt": past}}
            },
            "cidB": {
                "contract": {"payload": {"round": {"number": "11"}, "opensAt": future}}
            },
        }
    }
    payload, count = activities._latest_open_round(resp)
    assert count == 2
    assert activities._round_number(payload) == 10  # the open one, not 11 (future)


def test_open_rounds_entries_handles_map_and_list() -> None:
    entry = {"contract": {"payload": {"round": {"number": "1"}}}}
    as_map = {"open_mining_rounds": {"cid": entry}}
    as_list = {"open_mining_rounds": [entry]}
    assert activities.open_rounds_entries(as_map) == [entry]
    assert activities.open_rounds_entries(as_list) == [entry]
    assert activities.open_rounds_entries({}) == []


def test_count_scans_dedupes_sv_names() -> None:
    resp = {
        "scans": [
            {"scans": [{"svName": "DA"}, {"svName": "DTCC"}]},
            {"scans": [{"svName": "DA"}, {"svName": "Nasdaq"}]},
        ]
    }
    assert activities._count_scans(resp) == 3  # DA deduped


def test_count_sequencers_dedupes_ids() -> None:
    resp = {
        "dso_sequencers": [
            {"sequencers": [{"id": "s1"}, {"id": "s2"}]},
            {"sequencers": [{"id": "s2"}, {"id": "s3"}]},
        ]
    }
    assert activities._count_sequencers(resp) == 3


def test_count_sequencers_camelcase_key() -> None:
    """The live API returns ``domainSequencers`` (camelCase)."""
    resp = {
        "domainSequencers": [
            {"domainId": "d", "sequencers": [{"id": "s1"}, {"id": "s2"}, {"id": "s2"}]},
        ]
    }
    assert activities._count_sequencers(resp) == 2


# ---------------------------------------------------------------------------
# Throughput pure helpers
# ---------------------------------------------------------------------------


def test_rounds_per_second_measures_cadence() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = t0 + timedelta(seconds=600)
    resp = {
        "open_mining_rounds": [
            {
                "contract": {
                    "payload": {"round": {"number": 1}, "opensAt": t0.isoformat()}
                }
            },
            {
                "contract": {
                    "payload": {"round": {"number": 2}, "opensAt": t1.isoformat()}
                }
            },
        ]
    }
    assert throughput_canton._rounds_per_second(resp) == pytest.approx(1 / 600)


def test_rounds_per_second_falls_back_to_nominal_with_one_round() -> None:
    resp = {
        "open_mining_rounds": [{"contract": {"payload": {"round": 1, "opensAt": "x"}}}]
    }
    assert throughput_canton._rounds_per_second(resp) == pytest.approx(1 / 600.0)


def test_amulet_price_reads_highest_round() -> None:
    resp = {
        "open_mining_rounds": [
            {"contract": {"payload": {"round": {"number": 1}, "amuletPrice": "0.004"}}},
            {"contract": {"payload": {"round": {"number": 2}, "amuletPrice": "0.005"}}},
        ]
    }
    assert throughput_canton._amulet_price(resp) == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Mocked fetch activities
# ---------------------------------------------------------------------------

_SCAN_URL = "https://scan.example"


def _settings() -> CantonSettings:
    return CantonSettings(
        scan_urls=[_SCAN_URL],
        updates_window_seconds=60,
        updates_page_size=1000,
        validator_license_page_size=1000,
        validator_license_max_pages=50,
    )


def _patch_settings(mocker: MockerFixture) -> None:
    settings = _settings()
    # Bound at import in these modules + re-imported locally in _count_validators.
    from cert_ra.metrics.canton import scan_client as scan_client_mod
    from cert_ra.settings import canton as canton_settings_mod

    mocker.patch.object(scan_client_mod, "get_canton_settings", return_value=settings)
    mocker.patch.object(throughput_canton, "get_canton_settings", return_value=settings)
    mocker.patch.object(
        canton_settings_mod, "get_canton_settings", return_value=settings
    )


def _install_transport(mocker: MockerFixture, handler: httpx.MockTransport) -> None:
    from cert_ra.metrics.canton import scan_client as scan_client_mod

    original = httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = handler
        return original(*args, **kwargs)  # type: ignore[arg-type]

    mocker.patch.object(scan_client_mod.httpx, "AsyncClient", side_effect=factory)


def _now_iso(delta_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=delta_seconds)).isoformat()


async def test_fetch_canton_finality_happy_path(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    opens_at = _now_iso(-90)
    closes_at = _now_iso(510)  # 600s window

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/dso":
            return httpx.Response(
                200,
                json={
                    "voting_threshold": "4",
                    "sv_node_states": [{}, {}, {}, {}, {}],  # 5 SVs
                },
            )
        if path == "/v0/open-and-issuing-mining-rounds":
            return httpx.Response(
                200,
                json={
                    "open_mining_rounds": [
                        {
                            "contract": {
                                "payload": {
                                    "round": {"number": "100"},
                                    "opensAt": opens_at,
                                    "targetClosesAt": closes_at,
                                }
                            }
                        }
                    ]
                },
            )
        if path == "/v0/state/acs/snapshot-timestamp":
            return httpx.Response(200, json={"record_time": _now_iso(-5)})
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_canton_finality()
    assert result.latest_round_number == 100
    assert result.open_round_count == 1
    assert result.live_sv_count == 5
    assert result.voting_threshold == 4
    assert result.sv_quorum_margin == 1  # 5 - 4
    assert result.round_window_seconds == pytest.approx(600, abs=1)
    assert result.round_advance_seconds > 0
    assert result.ledger_freshness_seconds > 0


async def test_fetch_canton_finality_raises_without_round(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/dso":
            return httpx.Response(
                200, json={"voting_threshold": 4, "sv_node_states": []}
            )
        if path == "/v0/open-and-issuing-mining-rounds":
            return httpx.Response(200, json={"open_mining_rounds": []})
        if path == "/v0/state/acs/snapshot-timestamp":
            return httpx.Response(200, json={"record_time": _now_iso()})
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="no mining round"):
        await activities.fetch_canton_finality()


async def test_fetch_canton_decentralization_computes_gov_nakamoto(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/dso":
            return httpx.Response(200, json={"voting_threshold": "4"})
        if path == "/v0/scans":
            return httpx.Response(
                200,
                json={
                    "scans": [
                        {
                            "scans": [
                                {"svName": "DA"},
                                {"svName": "DTCC"},
                                {"svName": "Nasdaq"},
                                {"svName": "Broadridge"},
                                {"svName": "Circle"},
                            ]
                        }
                    ]
                },
            )
        if path == "/v0/dso-sequencers":
            return httpx.Response(
                200,
                json={"dso_sequencers": [{"sequencers": [{"id": "s1"}, {"id": "s2"}]}]},
            )
        if path == "/v0/admin/validator/licenses":
            return httpx.Response(
                200,
                json={
                    "validator_licenses": [{"payload": {}} for _ in range(42)],
                    "next_page_token": None,
                },
            )
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_canton_decentralization()
    assert result.sv_count == 5
    assert result.voting_threshold == 4
    assert result.validator_count == 42
    assert result.distinct_sequencer_count == 2
    # N=5: safety = floor(5/3)+1 = 2; liveness = 5 - 4 + 1 = 2
    assert result.gov_nakamoto_safety == 2
    assert result.gov_nakamoto_liveness == 2


async def test_fetch_canton_throughput_maps_scan_fields(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    t0 = _now_iso(-600)
    t1 = _now_iso(0)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/open-and-issuing-mining-rounds":
            return httpx.Response(
                200,
                json={
                    "open_mining_rounds": [
                        {
                            "contract": {
                                "payload": {
                                    "round": {"number": "1"},
                                    "opensAt": t0,
                                    "amuletPrice": "0.004",
                                }
                            }
                        },
                        {
                            "contract": {
                                "payload": {
                                    "round": {"number": "2"},
                                    "opensAt": t1,
                                    "amuletPrice": "0.005",
                                }
                            }
                        },
                    ]
                },
            )
        if path == "/v2/updates":
            return httpx.Response(200, json={"transactions": [{} for _ in range(30)]})
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await throughput_canton.fetch_canton_throughput()
    assert result.chain == "CANTON"
    assert result.gas_price == pytest.approx(0.005)  # highest round's amuletPrice
    assert result.transactions_per_second == pytest.approx(30 / 60)  # 30 updates / 60s
    assert result.blocks_per_second == pytest.approx(1 / 600, rel=0.05)


async def test_fetch_canton_finality_falls_back_to_dso_latest_round(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)
    opens_at = _now_iso(-30)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/dso":
            return httpx.Response(
                200,
                json={
                    "voting_threshold": 3,
                    "sv_node_states": [{}, {}, {}],
                    "latest_mining_round": {
                        "contract": {
                            "payload": {"round": {"number": "77"}, "opensAt": opens_at}
                        }
                    },
                },
            )
        if path == "/v0/open-and-issuing-mining-rounds":
            return httpx.Response(200, json={"open_mining_rounds": []})
        if path == "/v0/state/acs/snapshot-timestamp":
            return httpx.Response(500)  # freshness unavailable → sentinel
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_canton_finality()
    assert result.latest_round_number == 77  # from dso fallback
    assert result.open_round_count == 0
    assert result.round_window_seconds == -1.0  # no targetClosesAt
    assert result.ledger_freshness_seconds == -1.0  # snapshot-timestamp failed


async def test_fetch_canton_decentralization_sv_count_fallback(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/dso":
            return httpx.Response(
                200, json={"voting_threshold": 2, "sv_node_states": [{}, {}, {}]}
            )
        if path == "/v0/scans":
            return httpx.Response(200, json={"scans": []})  # empty → fall back to dso
        if path == "/v0/dso-sequencers":
            return httpx.Response(200, json={"dso_sequencers": []})
        if path == "/v0/admin/validator/licenses":
            # Two pages exercise the pagination loop.
            if request.url.params.get("after") == "p2":
                return httpx.Response(
                    200, json={"validator_licenses": [{}], "next_page_token": None}
                )
            return httpx.Response(
                200, json={"validator_licenses": [{}, {}], "next_page_token": "p2"}
            )
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await activities.fetch_canton_decentralization()
    assert result.sv_count == 3  # from sv_node_states fallback
    assert result.validator_count == 3  # 2 + 1 across two pages
    assert result.distinct_sequencer_count == 0


async def test_fetch_canton_throughput_handles_updates_failure(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/open-and-issuing-mining-rounds":
            return httpx.Response(
                200,
                json={
                    "open_mining_rounds": [
                        {"contract": {"payload": {"round": 1, "amuletPrice": "0.01"}}}
                    ]
                },
            )
        if path == "/v2/updates":
            return httpx.Response(500)  # updates unavailable → sentinel
        raise AssertionError(f"unexpected path {path}")

    _install_transport(mocker, httpx.MockTransport(handler))

    result = await throughput_canton.fetch_canton_throughput()
    assert result.transactions_per_second == -1.0
    assert result.gas_price == pytest.approx(0.01)
    assert result.blocks_per_second == pytest.approx(
        1 / 600.0
    )  # single round → nominal


async def test_scan_client_raises_without_urls(mocker: MockerFixture) -> None:
    from cert_ra.metrics.canton import scan_client as scan_client_mod

    mocker.patch.object(
        scan_client_mod,
        "get_canton_settings",
        return_value=CantonSettings(scan_urls=[]),
    )
    async with CantonScanClient() as scan:
        with pytest.raises(scan_client_mod.CantonScanError, match="no scan URLs"):
            await scan.get_dso()


async def test_scan_client_falls_back_across_urls(mocker: MockerFixture) -> None:
    from cert_ra.metrics.canton import scan_client as scan_client_mod

    mocker.patch.object(
        scan_client_mod,
        "get_canton_settings",
        return_value=CantonSettings(
            scan_urls=["https://down.example", "https://up.example"]
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "down.example":
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    _install_transport(mocker, httpx.MockTransport(handler))
    async with CantonScanClient() as scan:
        body: Any = await scan.get_dso()
    assert body == {"ok": True}
