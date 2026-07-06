# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Shared hourly Temporal workflow for state-row cleanup.

Loops over a registered list of ``(table, retention_clause)`` tuples and
runs a DELETE for each. Failed deletes for one table do NOT block the
rest of the iteration — each is wrapped in its own activity invocation
with its own retry policy.

Per the OIDC SSO implementation plan (PR-1):
- This file is the shared scheduler shell.
- PR-1 registers PR-1's tables in ``CLEANUP_TARGETS`` below.
- Each later PR appends its tables to the same list. One cron entry,
  one workflow, growing loop.

Worker registration (mounting on the Temporal worker) is deferred to
PR-2/PR-3 when the actual cleanup matters operationally. PR-1 ships the
shell so reviewers can see the pattern and the registered targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from cert_ra.workflows import cleanup_activities



@dataclass(frozen=True)
class CleanupTarget:
    """A single table to sweep on the hourly cleanup pass.

    Attributes:
        table: Tablename (snake_case) to delete from.
        retention_clause: SQL WHERE-clause fragment selecting rows to
            delete. Typically ``"expires_at < now() - INTERVAL '1 day'"``
            or similar. Must include parentheses if compound.
    """

    table: str
    retention_clause: str


# Registered cleanup targets. Each later PR APPENDS to this list.
CLEANUP_TARGETS: list[CleanupTarget] = [
    # PR-1 — token-hash + per-attempt state tables. All rows past their
    # natural TTL plus a 1-day diagnostic buffer.
    CleanupTarget(
        table="pending_oidc_link",
        retention_clause="expires_at < now() - INTERVAL '1 day'",
    ),
    CleanupTarget(
        table="pending_provider_switch",
        retention_clause="expires_at < now() - INTERVAL '1 day'",
    ),
    CleanupTarget(
        table="mfa_attempt",
        retention_clause="expires_at < now() - INTERVAL '1 day'",
    ),
    CleanupTarget(
        table="user_unlock_token",
        retention_clause="expires_at < now() - INTERVAL '1 day'",
    ),
    CleanupTarget(
        table="user_password_reset_token",
        retention_clause="expires_at < now() - INTERVAL '1 day'",
    ),
    # auth_attempt_log is a per-IP audit trail; keep 7 days for forensics.
    CleanupTarget(
        table="auth_attempt_log",
        retention_clause="attempted_at < now() - INTERVAL '7 days'",
    ),
    # user_lockout: expired lockout rows past 1 day are noise.
    CleanupTarget(
        table="user_lockout",
        retention_clause=(
            "locked_until IS NOT NULL AND locked_until < now() - INTERVAL '1 day'"
        ),
    ),
    # team_invitation: accepted or revoked + 30 days.
    CleanupTarget(
        table="team_invitation",
        retention_clause=(
            "(accepted_at IS NOT NULL OR revoked_at IS NOT NULL) "
            "AND COALESCE(accepted_at, revoked_at) < now() - INTERVAL '30 days'"
        ),
    ),
    # Note: operator_audit has its own retention (1 year) and is added
    # by PR-8 directly via DB-level policy; not in this loop.
]


_PER_TABLE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
)

_PER_TABLE_TIMEOUT = timedelta(minutes=5)


@workflow.defn
class StateRowCleanupWorkflow:
    """Hourly sweep that deletes expired rows from each registered table.

    Failures for one table do NOT abort the workflow — the next table
    still gets its sweep. The workflow returns the per-table counts for
    operational visibility.
    """

    @workflow.run
    async def run(self) -> dict[str, int]:
        """Run the sweep across every CLEANUP_TARGETS entry.

        Returns:
            Mapping of tablename to row count deleted (or ``-1`` if the
            activity raised, after retries exhausted).
        """
        results: dict[str, int] = {}
        for target in CLEANUP_TARGETS:
            try:
                deleted = await workflow.execute_activity(
                    cleanup_activities.delete_expired_rows,
                    args=(target.table, target.retention_clause),
                    start_to_close_timeout=_PER_TABLE_TIMEOUT,
                    retry_policy=_PER_TABLE_RETRY,
                )
                results[target.table] = int(deleted)
            except Exception:  # noqa: BLE001 — one failure must not block other tables
                workflow.logger.exception(
                    "cleanup activity failed for %s", target.table
                )
                results[target.table] = -1
        return results
