#!/usr/bin/env python3
"""Force-reset a user's password in the LOCAL database — DEBUG ONLY.

⚠️  This is a developer convenience for local debugging. It bypasses the
normal "verify current password" / "email reset token" flows entirely and
overwrites the stored hash directly. It also clears active account lockouts
and reactivates the account so you can actually log back in.

It lives under ``scripts/`` ON PURPOSE: the production container installs only
the built wheel (see ``docker/Dockerfile`` — ``uv pip install /tmp/*.whl``),
and ``scripts/`` is not part of the ``cert_ra`` package, so this file never
ships in a release image. Do NOT move it under ``src/cert_ra/``.

Usage:
    # prompt for the new password (not echoed)
    uv run python scripts/reset_password.py seth@certora.com

    # or pass it inline (visible in shell history — prefer the prompt)
    uv run python scripts/reset_password.py seth@certora.com --password 'c34T1R67&GG'

The target database is read from ``CERT_RA_DB_URL`` in the project ``.env``
file. As a safety interlock the script refuses to run unless that same ``.env``
sets ``CERT_RA_APP_DEBUG=true`` — i.e. it will only ever touch a database that
is explicitly configured for debug use.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import undefer_group

from cert_ra.api.lib import crypt
from cert_ra.db.models import User
from cert_ra.db.models.user_lockout import UserLockout

# Project root is two levels up from scripts/reset_password.py.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def _load_env() -> dict[str, str | None]:
    """Read key/value pairs from the project ``.env`` file."""
    if not _ENV_PATH.exists():
        print(f"✗ No .env file found at {_ENV_PATH}", file=sys.stderr)  # noqa: T201
        raise SystemExit(2)
    return dotenv_values(_ENV_PATH)


def _resolve_target(env: dict[str, str | None]) -> str:
    """Return the DB URL, refusing unless ``CERT_RA_APP_DEBUG`` is true.

    Raises:
        SystemExit: if debug mode is off or no DB URL is configured.
    """
    debug = (env.get("CERT_RA_APP_DEBUG") or "").strip().lower()
    if debug not in _TRUE_VALUES:
        print(  # noqa: T201
            "✗ Refusing to run: CERT_RA_APP_DEBUG is not set to true in "
            f"{_ENV_PATH}.\n"
            "  This force-reset is only permitted against a debug database.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    url = (env.get("CERT_RA_DB_URL") or "").strip()
    if not url:
        print(  # noqa: T201
            f"✗ CERT_RA_DB_URL is not set in {_ENV_PATH}.", file=sys.stderr
        )
        raise SystemExit(2)
    return url


async def reset_password(url: str, email: str, new_password: str) -> None:
    """Overwrite ``email``'s password hash and clear any active lockout."""
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(User)
                .where(User.email == email)
                .options(undefer_group("security_sensitive"))
            )
            user = result.scalar_one_or_none()
            if user is None:
                print(f"✗ No user found with email {email!r}", file=sys.stderr)  # noqa: T201
                raise SystemExit(1)

            user.hashed_password = await crypt.get_password_hash(new_password)
            user.is_active = True

            # Clear lockouts so the reset password actually lets you back in.
            await session.execute(
                delete(UserLockout).where(UserLockout.user_id == user.id)
            )
            user.has_active_lockout = False

            await session.commit()

        print(  # noqa: T201
            f"✓ Password reset for {email} "
            f"(superuser={user.is_superuser}, reactivated + lockouts cleared)"
        )
    finally:
        await engine.dispose()


def main() -> None:
    """Parse arguments and run the reset."""
    parser = argparse.ArgumentParser(
        description="Force-reset a local user's password (DEBUG ONLY).",
    )
    parser.add_argument("email", help="Email of the user to reset.")
    parser.add_argument(
        "--password",
        help="New password. Omit to be prompted (recommended).",
    )
    args = parser.parse_args()

    # Read the project .env directly. The script will only touch the database
    # named by CERT_RA_DB_URL, and only if CERT_RA_APP_DEBUG is true there.
    env = _load_env()
    url = _resolve_target(env)

    new_password = args.password
    if not new_password:
        new_password = getpass.getpass("New password: ")
        if new_password != getpass.getpass("Confirm new password: "):
            print("✗ Passwords do not match.", file=sys.stderr)  # noqa: T201
            raise SystemExit(1)
    if not new_password:
        print("✗ Empty password.", file=sys.stderr)  # noqa: T201
        raise SystemExit(1)

    asyncio.run(reset_password(url, args.email, new_password))


if __name__ == "__main__":
    main()
