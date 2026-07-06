# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the market collector / scorer activities.

These tests stay strictly at the unit boundary: ``run_yarn`` is mocked
to return raw bytes, and ``session_factory`` is mocked to return a fake
session that records what would have been inserted. No real DB or
yarn subprocess is involved.

What we cover:

* ``_parse_collector_output`` and ``_parse_scorer_output`` handle valid
  JSON, malformed JSON, and wrong-shape inputs cleanly.
* ``collect_market_snapshot`` adds the right model to the session and
  commits.
* ``score_market_snapshot`` does the same plus enforces the "score
  block must be present" rule that prevents a downstream DB CHECK
  violation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cert_ra.db.models import AutomatedMarketSnapshot
from cert_ra.metrics.market.activities import (
    MarketSnapshotPayloadError,
    _parse_collector_output,
    _parse_scorer_output,
    collect_market_snapshot,
    score_market_snapshot,
)
from cert_ra.metrics.market.schemas import MarketTickRef
from cert_ra.types import MarketSnapshotKind

if TYPE_CHECKING:
    from typing import Self

    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio backend (matches asyncpg)."""
    return "asyncio"


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _ref() -> MarketTickRef:
    return MarketTickRef(
        market_config_id=uuid4(),
        protocol="aave",
        chain_id=1,
        market_id_hex="0x" + "a" * 40,
        label="Aave USDC",
    )


class _FakeSession:
    """Minimal stand-in for ``AsyncSession`` for activity-level tests.

    Records every ``.add()`` call so the test can assert on what would
    have been persisted. ``commit`` / ``flush`` are no-op AsyncMocks.

    ``get`` returns ``self.market`` when called with ``MarketConfig``;
    the scorer activity uses ``session.get(MarketConfig, cfg.id)`` to
    rehydrate the market for protocol lookup. Tests configure
    ``self.market`` before invoking the activity.
    """

    def __init__(self) -> None:
        self.added: list = []
        self.commit = AsyncMock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.market: object | None = None

    def add(self, instance: object) -> None:
        # Stamp a deterministic id on AutomatedMarketSnapshot so the
        # scorer's MarketScore.source_amk_snapshot_id can reference it.
        from uuid import uuid4

        if getattr(instance, "id", None) is None:
            try:
                instance.id = uuid4()  # type: ignore[attr-defined]
            except AttributeError:
                pass
        self.added.append(instance)

    async def get(self, model: object, _id: object) -> object | None:
        return self.market

    async def scalars(self, _stmt: object) -> object:
        result = MagicMock()
        result.all.return_value = []
        return result

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _patch_session(mocker: MockerFixture) -> _FakeSession:
    """Make the activity's ``session_factory()`` return a FakeSession."""
    fake = _FakeSession()
    # session_factory()() — two-call shape used by all metrics activities.
    factory = MagicMock(return_value=fake)
    mocker.patch(
        "cert_ra.metrics.market.activities.session_factory",
        return_value=factory,
    )
    return fake


def _patch_yarn(mocker: MockerFixture, *, returns: str) -> object:
    return mocker.patch(
        "cert_ra.metrics.market.activities.run_yarn", return_value=returns
    )


# ---------------------------------------------------------------------------
# _parse_collector_output
# ---------------------------------------------------------------------------


def test_parse_collector_accepts_well_formed_payload() -> None:
    payload = _parse_collector_output(
        '{"anchors": {"a": 1}, "modifiers": {"a": "x"}}', _ref()
    )
    assert payload.anchors == {"a": 1}
    assert payload.modifiers == {"a": "x"}


def test_parse_collector_defaults_missing_keys_to_empty_dicts() -> None:
    payload = _parse_collector_output('{"anchors": {"a": 1}}', _ref())
    assert payload.anchors == {"a": 1}
    assert payload.modifiers == {}


def test_parse_collector_raises_on_invalid_json() -> None:
    with pytest.raises(MarketSnapshotPayloadError, match="not valid JSON"):
        _parse_collector_output("not json", _ref())


def test_parse_collector_raises_when_anchors_not_a_dict() -> None:
    with pytest.raises(MarketSnapshotPayloadError, match="expected shape"):
        _parse_collector_output('{"anchors": [1, 2, 3]}', _ref())


# ---------------------------------------------------------------------------
# _parse_scorer_output
# ---------------------------------------------------------------------------


def test_parse_scorer_accepts_full_payload() -> None:
    raw = (
        '{"anchors": {"a": 1}, "modifiers": {"a": "x"}, '
        '"score": {"anchors": {"k": {"pd": 0.5}}}}'
    )
    payload = _parse_scorer_output(raw, _ref())
    assert payload.anchors == {"a": 1}
    assert payload.modifiers == {"a": "x"}
    assert payload.score == {"anchors": {"k": {"pd": 0.5}}}


def test_parse_scorer_defaults_anchors_and_modifiers() -> None:
    payload = _parse_scorer_output('{"score": {"anchors": {}}}', _ref())
    assert payload.anchors == {}
    assert payload.modifiers == {}
    assert payload.score == {"anchors": {}}


def test_parse_scorer_raises_on_invalid_json() -> None:
    with pytest.raises(MarketSnapshotPayloadError, match="not valid JSON"):
        _parse_scorer_output("{", _ref())


# ---------------------------------------------------------------------------
# collect_market_snapshot
# ---------------------------------------------------------------------------


async def test_collect_adds_snapshot_and_commits(mocker: MockerFixture) -> None:
    ref = _ref()
    _patch_yarn(
        mocker,
        returns='{"anchors": {"tvl": 1.0}, "modifiers": {"tvl": "block 100"}}',
    )
    session = _patch_session(mocker)

    await collect_market_snapshot(ref)

    assert session.commit.await_count == 1
    assert len(session.added) == 1
    snap = session.added[0]
    assert isinstance(snap, AutomatedMarketSnapshot)
    assert snap.market_config_id == ref.market_config_id
    assert snap.kind == MarketSnapshotKind.COLLECT
    assert snap.anchors == {"tvl": 1.0}
    assert snap.modifiers == {"tvl": "block 100"}
    assert snap.score is None


async def test_collect_passes_collect_mode_to_yarn(mocker: MockerFixture) -> None:
    ref = _ref()
    yarn_mock = _patch_yarn(mocker, returns='{"anchors": {}, "modifiers": {}}')
    _patch_session(mocker)

    await collect_market_snapshot(ref)

    args, kwargs = yarn_mock.call_args
    assert kwargs.get("mode") == "collect" or "collect" in args


async def test_collect_propagates_parse_error_without_db_write(
    mocker: MockerFixture,
) -> None:
    ref = _ref()
    _patch_yarn(mocker, returns="not json")
    session = _patch_session(mocker)

    with pytest.raises(MarketSnapshotPayloadError):
        await collect_market_snapshot(ref)

    assert session.commit.await_count == 0
    assert session.added == []


# ---------------------------------------------------------------------------
# score_market_snapshot
# ---------------------------------------------------------------------------


async def test_score_adds_snapshot_with_score_block(mocker: MockerFixture) -> None:
    ref = _ref()
    _patch_yarn(
        mocker,
        returns=(
            '{"anchors": {}, "modifiers": {}, "score": {"anchors": {"k": {"pd": 0.1}}}}'
        ),
    )
    session = _patch_session(mocker)
    # Scorer rehydrates the market via session.get and then asks the resolver
    # + assurance loader for weights — stub both with empty results.
    # assurance_protocol=None → the real load_protocol_assurance short-circuits
    # to [] without a DB query, so the assurance loader is exercised unmocked.
    session.market = MagicMock(
        id=ref.market_config_id, protocol=ref.protocol, assurance_protocol=None
    )
    mocker.patch(
        "cert_ra.metrics.market.activities.resolve_weighting_profile_entries",
        return_value=[],
    )

    await score_market_snapshot(ref)

    # Two transactions: one for the SCORE snapshot, one for the MarketScore.
    assert session.commit.await_count == 2
    assert len(session.added) == 2
    snap = session.added[0]
    assert snap.kind == MarketSnapshotKind.SCORE
    assert snap.score == {"anchors": {"k": {"pd": 0.1}}}
    pd_row = session.added[1]
    # Empty controls + assurance → terms force to 1.0; final = anchors_term.
    # Single pd=0.1 → 1 - (1 - 0.1) = 0.1.
    assert pd_row.market_config_id == ref.market_config_id
    assert float(pd_row.final_pd) == pytest.approx(0.1)


async def test_score_rejects_payload_without_score_block(
    mocker: MockerFixture,
) -> None:
    """Catch missing ``score`` block before DB CHECK violates.

    The DB CHECK requires score to be non-null when kind='SCORE'. The
    activity catches this early so we surface a clean
    MarketSnapshotPayloadError instead of an IntegrityError.
    """
    ref = _ref()
    _patch_yarn(mocker, returns='{"anchors": {}, "modifiers": {}}')
    session = _patch_session(mocker)

    with pytest.raises(MarketSnapshotPayloadError, match="no 'score' block"):
        await score_market_snapshot(ref)

    assert session.commit.await_count == 0
    assert session.added == []


async def test_score_passes_score_mode_to_yarn(mocker: MockerFixture) -> None:
    ref = _ref()
    yarn_mock = _patch_yarn(mocker, returns='{"score": {"anchors": {}}}')
    session = _patch_session(mocker)
    # assurance_protocol=None → the real load_protocol_assurance short-circuits
    # to [] without a DB query, so the assurance loader is exercised unmocked.
    session.market = MagicMock(
        id=ref.market_config_id, protocol=ref.protocol, assurance_protocol=None
    )
    mocker.patch(
        "cert_ra.metrics.market.activities.resolve_weighting_profile_entries",
        return_value=[],
    )

    await score_market_snapshot(ref)

    args, kwargs = yarn_mock.call_args
    assert kwargs.get("mode") == "score" or "score" in args


async def test_score_preserves_snapshot_when_pd_compute_fails(
    mocker: MockerFixture,
) -> None:
    """A MarketScoringError leaves transaction A committed but skips B."""
    from cert_ra.metrics.market.scoring import MarketScoringError

    ref = _ref()
    _patch_yarn(
        mocker,
        returns='{"anchors": {}, "modifiers": {"e": "x"}, '
        '"score": {"anchors": {"k": {"pd": 0.1}}}}',
    )
    session = _patch_session(mocker)
    # assurance_protocol=None → the real load_protocol_assurance short-circuits
    # to [] without a DB query, so the assurance loader is exercised unmocked.
    session.market = MagicMock(
        id=ref.market_config_id, protocol=ref.protocol, assurance_protocol=None
    )
    mocker.patch(
        "cert_ra.metrics.market.activities.resolve_weighting_profile_entries",
        return_value=[],
    )
    mocker.patch(
        "cert_ra.metrics.market.activities.compute_market_pd",
        side_effect=MarketScoringError("bad pd"),
    )

    # The activity should not re-raise — failures inside transaction B
    # are logged + swallowed.
    await score_market_snapshot(ref)

    # Only transaction A committed; only the SCORE snapshot was added.
    assert session.commit.await_count == 1
    assert len(session.added) == 1
    snap = session.added[0]
    assert snap.kind == MarketSnapshotKind.SCORE
    assert snap.modifiers == {"e": "x"}
