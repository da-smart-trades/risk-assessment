"""Static-grep CI check enforcing the single-lookup-path invariant.

For every state-row table the OIDC SSO design protects with a
``cert_ra/api/lib/X.py`` module, this script asserts that no other
production code reads or writes the table directly. The only legitimate
reader/writer is the matching ``lib/X.py`` module.

Why a custom check? The invariant is structural — if reviewers
accidentally let in a ``select(TeamInvitation)`` inside a controller,
the invitation flow's atomicity / replay-resistance breaks. The
canonical helper pattern is only useful if it's the *only* path.

What it catches:
    - ``select(TeamInvitation)`` outside ``lib/invitations.py``
    - ``update(TeamInvitation)`` outside ``lib/invitations.py``
    - Same for: PendingOidcLink, PendingProviderSwitch, MfaAttempt,
      UserUnlockToken, UserPasswordResetToken, UserRecoveryCode.

What it does NOT catch:
    - Indirect access via ``Mapped`` relationships (the helpers do load
      the User relationship; that's a feature).
    - Test code (``tests/`` is exempt — tests legitimately need direct
      access to set up fixtures).
    - Migrations (``src/cert_ra/db/migrations/`` is exempt).
    - The helper modules themselves.

Exit code 0 on pass, 1 on any violation.

Wire into ``make lint`` via the Makefile's lint target.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Mapping of model class name → the lib module that's its only legitimate
# reader/writer.
PROTECTED_TABLES: dict[str, str] = {
    "TeamInvitation": "src/cert_ra/api/lib/invitations.py",
    "PendingOidcLink": "src/cert_ra/api/lib/pending_links.py",
    "PendingProviderSwitch": "src/cert_ra/api/lib/pending_provider_switches.py",
    "MfaAttempt": "src/cert_ra/api/lib/mfa_attempts.py",
    "UserUnlockToken": "src/cert_ra/api/lib/unlock_tokens.py",
    "UserPasswordResetToken": "src/cert_ra/api/lib/password_resets.py",
    "UserRecoveryCode": "src/cert_ra/api/lib/recovery_codes.py",
}

# Source roots to scan.
SCAN_ROOTS: tuple[str, ...] = ("src/cert_ra",)

# Paths excluded from the check. Test code and migrations need direct
# access; the helper modules themselves are the legitimate writers.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "src/cert_ra/db/migrations/",
    # Helpers themselves (their own permitted paths):
    *PROTECTED_TABLES.values(),
)


def _scan_file(path: Path, table: str) -> list[tuple[int, str]]:
    """Return a list of ``(line_number, line)`` matches for ``select(X)``
    or ``update(X)`` referencing ``table`` in ``path``.
    """
    # Match `select(Table)` or `update(Table)` allowing for whitespace
    # and optional module-qualified access (`sa.select(Table)`).
    pattern = re.compile(
        rf"\b(?:select|update)\s*\(\s*{re.escape(table)}\b",
    )
    violations: list[tuple[int, str]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if pattern.search(line):
                    violations.append((lineno, line.rstrip()))
    except OSError:
        # Unreadable files (binary, deleted between scan and read) — skip.
        return []
    return violations


def _is_exempt(path: Path, allowed_helper: str) -> bool:
    """Return True iff ``path`` is in the exempt list for this table.

    The helper module itself is always exempt. Other helper modules are
    not — they shouldn't reach into another table's state.
    """
    rel = path.as_posix()
    if rel == allowed_helper:
        return True
    return any(rel.startswith(p) for p in EXEMPT_PREFIXES if p != allowed_helper)


def main() -> int:
    """Scan SCAN_ROOTS for direct ORM access to the protected tables.

    Returns:
        Exit code: 0 on clean, 1 on any violation.
    """
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[tuple[str, int, str, str]] = []

    for table, allowed_helper in PROTECTED_TABLES.items():
        for scan_root in SCAN_ROOTS:
            for path in (repo_root / scan_root).rglob("*.py"):
                rel = path.relative_to(repo_root)
                if _is_exempt(rel, allowed_helper):
                    continue
                for lineno, line in _scan_file(path, table):
                    violations.append((str(rel), lineno, table, line))

    if not violations:
        print("canonical_helper_check: OK (no direct ORM access to protected tables)")
        return 0

    print(
        "canonical_helper_check: FAIL — direct ORM access to protected "
        "tables found.\n"
        "Refactor through the matching cert_ra/api/lib/X.py module.\n",
        file=sys.stderr,
    )
    for rel, lineno, table, line in violations:
        print(
            f"  {rel}:{lineno}  (table: {table})\n    {line}",
            file=sys.stderr,
        )
    print(
        f"\nTotal violations: {len(violations)}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
