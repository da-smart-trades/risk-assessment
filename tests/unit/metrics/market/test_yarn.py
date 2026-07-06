# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unit tests for the yarn subprocess wrapper.

The wrapper is the boundary between Temporal activities and the external
yarn CLI. We patch ``asyncio.create_subprocess_exec`` so the tests run
without a real yarn binary, and exercise every error path the activity
layer relies on for retry semantics.
"""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import pytest

from cert_ra.metrics.market.yarn import (
    YarnExitError,
    YarnInputError,
    YarnInvocation,
    YarnTimeoutError,
    run_yarn,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Restrict anyio tests to asyncio (asyncpg's loop)."""
    return "asyncio"


# ---------------------------------------------------------------------------
# Fake subprocess plumbing
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Minimal stand-in for ``asyncio.subprocess.Process`` used in tests.

    ``communicate_behavior`` controls how ``communicate`` resolves: it can
    return ``(stdout, stderr)`` directly, raise an exception, or sleep
    forever (so ``asyncio.wait_for`` triggers the timeout path).
    """

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
        raise_during_communicate: Exception | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode: int | None = returncode if not hang else None
        self._hang = hang
        self._raise = raise_during_communicate
        self.terminate_called = False
        self.kill_called = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._raise is not None:
            raise self._raise
        if self._hang:
            await asyncio.sleep(3600)
        return (self._stdout, self._stderr)

    def terminate(self) -> None:
        self.terminate_called = True
        # Simulate the kernel reaping the process after SIGTERM.
        self.returncode = -signal.SIGTERM

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -signal.SIGKILL

    async def wait(self) -> int:
        # After terminate(), returncode is set, so wait can complete instantly.
        if self.returncode is None:
            await asyncio.sleep(3600)
        return self.returncode


def _patch_settings(mocker: MockerFixture, *, cwd: str | None = "/srv/yarn") -> None:
    from cert_ra.settings.market import MarketMetricsSettings

    mocker.patch(
        "cert_ra.metrics.market.yarn.get_market_metrics_settings",
        return_value=MarketMetricsSettings(yarn_cwd=cwd, yarn_timeout_seconds=0.5),
    )


def _patch_subprocess(mocker: MockerFixture, fake: _FakeProcess) -> object:
    """Make ``create_subprocess_exec`` return ``fake`` and record the argv."""

    async def _factory(*args: str, **_kwargs: object) -> _FakeProcess:
        _factory.argv = list(args)  # type: ignore[attr-defined]
        _factory.cwd = _kwargs.get("cwd")  # type: ignore[attr-defined]
        return fake

    _factory.argv = []  # type: ignore[attr-defined]
    _factory.cwd = None  # type: ignore[attr-defined]
    mocker.patch("asyncio.create_subprocess_exec", side_effect=_factory)
    return _factory


# ---------------------------------------------------------------------------
# Argv validation
# ---------------------------------------------------------------------------


async def test_argv_rejects_non_lowercase_protocol(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    with pytest.raises(YarnInputError, match="lower-snake-case"):
        await run_yarn(
            YarnInvocation(protocol="AAVE", chain_id=1, market_id_hex="0xabc"),
            mode="collect",
        )


async def test_argv_rejects_non_hex_market_id(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    with pytest.raises(YarnInputError, match="0x"):
        await run_yarn(
            YarnInvocation(protocol="aave", chain_id=1, market_id_hex="not-hex"),
            mode="collect",
        )


async def test_argv_includes_score_flag_in_score_mode(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(stdout=b"{}")
    factory = _patch_subprocess(mocker, fake)
    await run_yarn(
        YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
        mode="score",
    )
    assert factory.argv[:5] == ["yarn", "--silent", "aave", "--score", "--llm"]
    # chain_id is stringified
    assert "1" in factory.argv
    assert "0xabc" in factory.argv


async def test_argv_omits_score_flag_in_collect_mode(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(stdout=b"{}")
    factory = _patch_subprocess(mocker, fake)
    await run_yarn(
        YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
        mode="collect",
    )
    assert "--score" not in factory.argv
    assert factory.argv[:5] == ["yarn", "--silent", "aave", "--llm", "claude"]


# ---------------------------------------------------------------------------
# Success / exit / timeout paths
# ---------------------------------------------------------------------------


async def test_run_yarn_returns_stdout_on_success(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(stdout=b'{"metrics": {}}', stderr=b"")
    _patch_subprocess(mocker, fake)
    out = await run_yarn(
        YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
        mode="collect",
    )
    assert out == '{"metrics": {}}'


async def test_run_yarn_uses_settings_cwd(mocker: MockerFixture) -> None:
    _patch_settings(mocker, cwd="/srv/yarn-project")
    fake = _FakeProcess(stdout=b"{}")
    factory = _patch_subprocess(mocker, fake)
    await run_yarn(
        YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
        mode="collect",
    )
    assert factory.cwd == "/srv/yarn-project"


async def test_run_yarn_explicit_cwd_overrides_settings(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker, cwd="/srv/default")
    fake = _FakeProcess(stdout=b"{}")
    factory = _patch_subprocess(mocker, fake)
    await run_yarn(
        YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
        mode="collect",
        cwd="/override",
    )
    assert factory.cwd == "/override"


async def test_run_yarn_raises_when_cwd_missing(mocker: MockerFixture) -> None:
    _patch_settings(mocker, cwd=None)
    with pytest.raises(RuntimeError, match="YARN_CWD"):
        await run_yarn(
            YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
            mode="collect",
        )


async def test_run_yarn_raises_yarn_exit_error_on_nonzero(
    mocker: MockerFixture,
) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(stdout=b"", stderr=b"boom", returncode=2)
    _patch_subprocess(mocker, fake)
    with pytest.raises(YarnExitError) as info:
        await run_yarn(
            YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
            mode="collect",
        )
    assert info.value.returncode == 2
    assert "boom" in info.value.stderr


async def test_run_yarn_times_out_and_terminates(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(hang=True)
    _patch_subprocess(mocker, fake)
    with pytest.raises(YarnTimeoutError):
        await run_yarn(
            YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
            mode="collect",
            timeout_seconds=0.1,
        )
    assert fake.terminate_called is True


async def test_run_yarn_terminates_on_cancellation(mocker: MockerFixture) -> None:
    _patch_settings(mocker)
    fake = _FakeProcess(hang=True)
    _patch_subprocess(mocker, fake)

    async def caller() -> None:
        await run_yarn(
            YarnInvocation(protocol="aave", chain_id=1, market_id_hex="0xabc"),
            mode="collect",
            timeout_seconds=10.0,
        )

    task = asyncio.create_task(caller())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.terminate_called is True
