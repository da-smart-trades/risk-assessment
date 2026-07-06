# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import ssl
from datetime import UTC

import pytest

from cert_ra.settings.db import DatabaseSettings

# Anything that reads DATABASE_* / CERT_RA_DB_* needs a clean env. The
# fixtures wipe both prefixes so the user's local .env can't leak into
# assertions about the model defaults / fallbacks.
_RELEVANT_ENV_VARS = (
    "CERT_RA_DB_URL",
    "CERT_RA_DB_SSL_MODE",
    "CERT_RA_DB_SSL_CA_PATH",
    "DATABASE_HOST",
    "DATABASE_PORT",
    "DATABASE_USER",
    "DATABASE_PASSWORD",
    "DATABASE_NAME",
)


@pytest.fixture(autouse=True)
def _clean_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _RELEVANT_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_default_url_when_no_env_vars_set() -> None:
    settings = DatabaseSettings()
    assert settings.url == "postgresql+asyncpg://localhost/certora-risk-assessment"
    assert settings.ssl_mode == "require"


def test_cert_ra_db_url_wins_over_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CERT_RA_DB_URL", "postgresql+asyncpg://explicit@host/db")
    monkeypatch.setenv("DATABASE_HOST", "should-be-ignored")
    monkeypatch.setenv("DATABASE_USER", "ignored")
    monkeypatch.setenv("DATABASE_PASSWORD", "ignored")
    settings = DatabaseSettings()
    assert settings.url == "postgresql+asyncpg://explicit@host/db"


def test_url_composed_from_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_HOST", "rds.example.com")
    monkeypatch.setenv("DATABASE_PORT", "5433")
    monkeypatch.setenv("DATABASE_USER", "svc")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret")
    monkeypatch.setenv("DATABASE_NAME", "cert_ra")
    settings = DatabaseSettings()
    assert (
        settings.url == "postgresql+asyncpg://svc:secret@rds.example.com:5433/cert_ra"
    )


def test_url_composition_url_encodes_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_HOST", "rds.example.com")
    monkeypatch.setenv("DATABASE_USER", "user@svc")
    monkeypatch.setenv("DATABASE_PASSWORD", "p@ss/word!")
    settings = DatabaseSettings()
    # @, /, and ! must be percent-encoded so they don't break URL parsing.
    assert "user%40svc" in settings.url
    assert "p%40ss%2Fword%21" in settings.url


def test_url_composition_defaults_port_and_db_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_HOST", "rds.example.com")
    monkeypatch.setenv("DATABASE_USER", "svc")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret")
    settings = DatabaseSettings()
    assert settings.url.endswith(":5432/cert_ra")


def test_url_composition_requires_all_three_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Host alone isn't enough — without USER+PASSWORD we'd build a
    # malformed URL, so the validator must fall back to the default.
    monkeypatch.setenv("DATABASE_HOST", "rds.example.com")
    settings = DatabaseSettings()
    assert "localhost" in settings.url


def test_ssl_param_disable() -> None:
    settings = DatabaseSettings(ssl_mode="disable")
    assert settings.build_ssl_param() is False


def test_ssl_param_require() -> None:
    settings = DatabaseSettings(ssl_mode="require")
    ctx = settings.build_ssl_param()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


def test_ssl_param_verify_full_requires_ca_path() -> None:
    with pytest.raises(ValueError, match="CERT_RA_DB_SSL_CA_PATH"):
        DatabaseSettings(ssl_mode="verify-full")


def _write_self_signed_pem(path) -> None:  # noqa: ANN001
    """Drop a freshly-minted self-signed cert at `path` for SSLContext to load."""
    from datetime import datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_ssl_param_verify_full_with_ca(tmp_path) -> None:  # noqa: ANN001
    cert = tmp_path / "ca.pem"
    _write_self_signed_pem(cert)
    settings = DatabaseSettings(ssl_mode="verify-full", ssl_ca_path=cert)
    ctx = settings.build_ssl_param()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_ssl_param_verify_ca_does_not_check_hostname(tmp_path) -> None:  # noqa: ANN001
    cert = tmp_path / "ca.pem"
    _write_self_signed_pem(cert)
    settings = DatabaseSettings(ssl_mode="verify-ca", ssl_ca_path=cert)
    ctx = settings.build_ssl_param()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_unknown_ssl_mode_raises() -> None:
    with pytest.raises(ValueError, match="Invalid ssl_mode"):
        DatabaseSettings(ssl_mode="bogus")
