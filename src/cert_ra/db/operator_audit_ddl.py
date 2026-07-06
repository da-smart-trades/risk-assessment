# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Append-only enforcement DDL for ``operator_audit`` (PR-8, AC #32).

The operator audit log must be immutable to the application: an operator
who compromises a session must not be able to UPDATE or DELETE the rows
that record their cross-customer actions. We enforce this at the database
level with a trigger rather than role grants because:

- a role-level ``REVOKE`` is bypassed by the table owner (which the test
  / dev connection is) and is environment-specific, and
- a blanket UPDATE/DELETE block would also break the ``ON DELETE SET
  NULL`` FK cascade on ``target_team_id`` / ``target_user_id`` (AC #121,
  customer churn must not destroy the audit row).

The trigger blocks every DELETE and every UPDATE **except** the FK
SET-NULL cascade (target_* moving toward NULL with no other column
changed). It applies to all roles, including the owner.

The same SQL is used in two places (single source of truth):
- a SQLAlchemy ``after_create`` event on ``OperatorAudit.__table__`` so
  the trigger exists when the schema is built via ``metadata.create_all``
  (tests / dev), and
- the Alembic migration that adds it to production databases.
"""

from __future__ import annotations

OPERATOR_AUDIT_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION operator_audit_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'operator_audit is append-only: DELETE is not permitted';
    END IF;
    IF NEW.actor_user_id <> OLD.actor_user_id
       OR NEW.actor_session_id <> OLD.actor_session_id
       OR NEW.actor_ip <> OLD.actor_ip
       OR NEW.action <> OLD.action
       OR NEW.payload::text <> OLD.payload::text
       OR (NEW.target_team_id IS NOT NULL
           AND NEW.target_team_id IS DISTINCT FROM OLD.target_team_id)
       OR (NEW.target_user_id IS NOT NULL
           AND NEW.target_user_id IS DISTINCT FROM OLD.target_user_id)
    THEN
        RAISE EXCEPTION
            'operator_audit is append-only: only the FK SET NULL cascade is permitted';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

OPERATOR_AUDIT_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER trg_operator_audit_append_only
    BEFORE UPDATE OR DELETE ON operator_audit
    FOR EACH ROW EXECUTE FUNCTION operator_audit_append_only();
"""

OPERATOR_AUDIT_APPEND_ONLY_DROP_TRIGGER = (
    "DROP TRIGGER IF EXISTS trg_operator_audit_append_only ON operator_audit;"
)

OPERATOR_AUDIT_APPEND_ONLY_DROP_FUNCTION = (
    "DROP FUNCTION IF EXISTS operator_audit_append_only();"
)

# asyncpg cannot run multiple SQL commands in one prepared statement, so
# the function and trigger are created/dropped as separate statements (in
# order) by both the model event and the migration.
OPERATOR_AUDIT_APPEND_ONLY_CREATE_STATEMENTS = (
    OPERATOR_AUDIT_APPEND_ONLY_FUNCTION,
    OPERATOR_AUDIT_APPEND_ONLY_TRIGGER,
)
OPERATOR_AUDIT_APPEND_ONLY_DROP_STATEMENTS = (
    OPERATOR_AUDIT_APPEND_ONLY_DROP_TRIGGER,
    OPERATOR_AUDIT_APPEND_ONLY_DROP_FUNCTION,
)
