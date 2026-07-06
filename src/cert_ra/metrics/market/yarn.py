# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Subprocess wrapper around the yarn-based market metrics CLI.

The collector and scorer Temporal activities shell out to a single yarn
binary that knows how to talk to chain RPC + LLM and emits its result as
JSON on stdout. Two CLI shapes are used:

* **List mode** — ``yarn <protocol>`` (no further args) prints a JSON
  array of every market the protocol currently exposes, each entry
  shaped ``{"protocol", "chainId", "marketId", "label"}``. The collector
  workflow calls this once per enabled protocol per tick to discover
  the (chain, market) set it should fan out across.
* **Collect / score mode** — ``yarn <protocol> [--score] --llm claude
  --output json <chain_id> <market_id_hex>`` runs against one specific
  market and emits the metrics/evidence/score JSON consumed by the
  per-market activities.

``run_yarn`` returns the raw stdout string on success; activities parse
and validate the JSON shape separately. Stderr is captured and surfaced
in the error message on non-zero exit so logs are actionable.

The wrapper enforces two timeouts:

* An ``asyncio.wait_for`` ceiling (``yarn_timeout_seconds`` setting,
  default 110s) that cleanly raises ``YarnTimeoutError``.
* A SIGTERM → 5s grace → SIGKILL cancellation path triggered on both
  timeout and explicit ``CancelledError`` so the Temporal worker can
  reclaim activity slots without orphaning child processes.

Non-zero exit becomes ``YarnExitError``; invalid argv (no protocol /
illegal characters) becomes ``YarnInputError`` raised synchronously
before spawning the subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Literal

from cert_ra.settings.market import get_market_metrics_settings

__all__ = (
    "YarnExitError",
    "YarnInputError",
    "YarnInvocation",
    "YarnTimeoutError",
    "run_yarn",
    "run_yarn_list",
)


_PROTOCOL_RE = re.compile(r"^[a-z0-9_-]+$")
"""Mirrors the ``ck_market_config_protocol_lowercase_kebab`` CHECK so the
subprocess can never be spawned with a protocol the DB would refuse."""

_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
"""Loose hex check: ``0x`` prefix followed by hex chars. Length is enforced
by the ``market_id_hex VARCHAR(66)`` column at insert time."""

_KILL_GRACE_SECONDS = 5.0


class YarnError(Exception):
    """Base class for yarn-subprocess failures."""


class YarnInputError(YarnError):
    """The arguments would not pass yarn argv validation."""


class YarnTimeoutError(YarnError):
    """The subprocess did not finish within ``yarn_timeout_seconds``."""


class YarnExitError(YarnError):
    """The subprocess exited with a non-zero return code."""

    def __init__(self, returncode: int, stderr: str) -> None:
        """Capture the non-zero exit code and the captured stderr."""
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"yarn exited {returncode}: {stderr[:500] if stderr else '<empty stderr>'}"
        )


@dataclass(frozen=True, slots=True)
class YarnInvocation:
    """Describes the market the yarn process should be invoked for."""

    protocol: str
    chain_id: int
    market_id_hex: str


def _validate(invocation: YarnInvocation) -> None:
    if not _PROTOCOL_RE.match(invocation.protocol):
        msg = (
            f"yarn protocol {invocation.protocol!r} is not lower-snake-case "
            f"(expected ``^[a-z0-9_-]+$``)"
        )
        raise YarnInputError(msg)
    if not _HEX_RE.match(invocation.market_id_hex):
        msg = (
            f"market_id_hex {invocation.market_id_hex!r} must start with 0x "
            f"and contain only hex characters"
        )
        raise YarnInputError(msg)


def _build_argv(
    invocation: YarnInvocation, mode: Literal["collect", "score"]
) -> list[str]:
    """Build the argv passed to ``asyncio.create_subprocess_exec``.

    Ordering matches the spec from the product owner::

        yarn --silent <protocol> [--score] --llm claude --output json <chain_id> <market_id_hex>

    ``--silent`` is a yarn-classic global flag (must precede the script
    name) that suppresses the ``yarn run v1.22.19`` banner and the
    ``$ <command>`` echo yarn otherwise writes to *stdout* ahead of the
    script's own output. Without it those lines pollute stdout and the
    JSON parse fails at line 1 column 1.
    """
    argv = ["yarn", "--silent", invocation.protocol]
    if mode == "score":
        argv.append("--score")
    argv.extend(
        [
            "--llm",
            "claude",
            "--output",
            "json",
            str(invocation.chain_id),
            invocation.market_id_hex,
        ]
    )
    return argv


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """SIGTERM → 5s grace → SIGKILL. Idempotent if the process already exited."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=_KILL_GRACE_SECONDS)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_KILL_GRACE_SECONDS)


async def run_yarn(
    invocation: YarnInvocation,
    mode: Literal["collect", "score"],
    *,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Run the yarn CLI for one market and return stdout (as ``str``).

    ``cwd`` and ``timeout_seconds`` default to the values from
    :class:`cert_ra.settings.market.MarketMetricsSettings`. The activity
    layer is responsible for parsing the returned JSON and persisting it.

    Args:
        invocation: protocol + chain id + market hex to query.
        mode: ``"collect"`` (no ``--score``) or ``"score"`` (with ``--score``).
        cwd: working directory of the yarn project. ``None`` falls back to
            ``MarketMetricsSettings.yarn_cwd``.
        timeout_seconds: per-invocation timeout ceiling. ``None`` falls
            back to ``MarketMetricsSettings.yarn_timeout_seconds``.

    Returns:
        The raw stdout produced by the yarn binary.

    Raises:
        YarnInputError: arguments would not pass argv validation.
        YarnTimeoutError: subprocess exceeded ``timeout_seconds``.
        YarnExitError: subprocess exited non-zero.
        RuntimeError: ``yarn_cwd`` is not configured.
    """
    _validate(invocation)
    settings = get_market_metrics_settings()
    effective_cwd = cwd if cwd is not None else settings.yarn_cwd
    if not effective_cwd:
        msg = (
            "CERT_RA_MARKET_METRICS_YARN_CWD is not configured; "
            "cannot spawn the yarn subprocess."
        )
        raise RuntimeError(msg)
    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.yarn_timeout_seconds
    )
    argv = _build_argv(invocation, mode)
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=effective_cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=effective_timeout
        )
    except TimeoutError as exc:
        await _terminate(process)
        msg = (
            f"yarn {invocation.protocol} did not finish within {effective_timeout:.1f}s"
        )
        raise YarnTimeoutError(msg) from exc
    except asyncio.CancelledError:
        await _terminate(process)
        raise

    stderr_text = stderr_bytes.decode(errors="replace")
    if process.returncode != 0:
        raise YarnExitError(process.returncode or -1, stderr_text)
    return stdout_bytes.decode(errors="replace")


async def run_yarn_list(
    protocol: str,
    *,
    cwd: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Run ``yarn <protocol>`` (no extra args) and return stdout.

    The yarn CLI prints a JSON array of every market the protocol
    currently exposes when invoked with no positional args; the workflow
    calls this once per enabled protocol per tick to discover the
    ``(chainId, marketId, label)`` set it should fan out collect/score
    activities across.

    Same timeout / SIGTERM-on-cancel semantics as :func:`run_yarn`.

    Args:
        protocol: Lower-snake-case protocol name (validated the same way
            as for :func:`run_yarn`).
        cwd: Yarn project working directory. ``None`` falls back to
            ``MarketMetricsSettings.yarn_cwd``.
        timeout_seconds: Per-invocation timeout ceiling. ``None`` falls
            back to ``MarketMetricsSettings.yarn_timeout_seconds``.

    Returns:
        The raw stdout produced by ``yarn <protocol>``.

    Raises:
        YarnInputError: ``protocol`` would not pass argv validation.
        YarnTimeoutError: Subprocess exceeded ``timeout_seconds``.
        YarnExitError: Subprocess exited non-zero.
        RuntimeError: ``yarn_cwd`` is not configured.
    """
    if not _PROTOCOL_RE.match(protocol):
        msg = (
            f"yarn protocol {protocol!r} is not lower-snake-case "
            f"(expected ``^[a-z0-9_-]+$``)"
        )
        raise YarnInputError(msg)
    settings = get_market_metrics_settings()
    effective_cwd = cwd if cwd is not None else settings.yarn_cwd
    if not effective_cwd:
        msg = (
            "CERT_RA_MARKET_METRICS_YARN_CWD is not configured; "
            "cannot spawn the yarn subprocess."
        )
        raise RuntimeError(msg)
    effective_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.yarn_timeout_seconds
    )
    process = await asyncio.create_subprocess_exec(
        "yarn",
        "--silent",
        protocol,
        cwd=effective_cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=effective_timeout
        )
    except TimeoutError as exc:
        await _terminate(process)
        msg = f"yarn {protocol} (list) did not finish within {effective_timeout:.1f}s"
        raise YarnTimeoutError(msg) from exc
    except asyncio.CancelledError:
        await _terminate(process)
        raise

    stderr_text = stderr_bytes.decode(errors="replace")
    if process.returncode != 0:
        raise YarnExitError(process.returncode or -1, stderr_text)
    return stdout_bytes.decode(errors="replace")
