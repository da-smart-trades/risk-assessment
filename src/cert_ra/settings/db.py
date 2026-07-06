# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import os
import ssl
from functools import cache
from pathlib import Path
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cert_ra.utils import PACKAGE_ROOT

_DEFAULT_URL = "postgresql+asyncpg://localhost/certora-risk-assessment"

# Modes mirror libpq's sslmode terminology so operators can reason about
# the dev/staging/prod posture using the same vocabulary they'd use with
# psql or any other Postgres client.
_VALID_SSL_MODES = frozenset({"disable", "require", "verify-ca", "verify-full"})


class DatabaseSettings(BaseSettings):
    """Database settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_db_", case_sensitive=False, extra="ignore"
    )

    url: str = _DEFAULT_URL
    """SQLAlchemy database URL.

    If unset (env var `CERT_RA_DB_URL` not exported), the URL is composed
    from `DATABASE_HOST` / `DATABASE_PORT` / `DATABASE_USER` /
    `DATABASE_PASSWORD` / `DATABASE_NAME` — the shape Fargate task
    definitions inject for the cert_ra app, workers, and migration tasks.
    """
    echo: bool = False
    """Enable SQLAlchemy engine query logs."""
    echo_pool: bool = False
    """Enable SQLAlchemy connection pool logs."""
    pool_disabled: bool = False
    """Disable connection pooling (uses NullPool)."""
    pool_max_overflow: int = 10
    """Max connections allowed beyond pool_size."""
    pool_size: int = 5
    """Number of persistent connections in the pool."""
    pool_timeout: int = 30
    """Seconds to wait before timing out a connection checkout."""
    pool_recycle: int = 300
    """Seconds before a connection is recycled."""
    pool_pre_ping: bool = False
    """Ping the database before each checkout to detect stale connections."""
    ssl_mode: str = "require"
    """Postgres TLS posture (libpq-style):

    - `disable`     — plaintext, no SSL.
    - `require`     — encrypt the connection but do not verify the cert.
                      Suitable for dev/self-signed and for matching RDS's
                      `rds.force_ssl=1` without shipping a CA bundle.
    - `verify-ca`   — encrypt + verify the server cert against
                      `ssl_ca_path`. Hostname is not checked.
    - `verify-full` — encrypt + verify cert + verify hostname.
    """
    ssl_ca_path: Path | None = None
    """Path to a PEM-encoded CA bundle. Required for `verify-ca` /
    `verify-full`. For RDS, point at the global trust bundle
    (https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem)."""
    migration_config: Path = PACKAGE_ROOT / "db/migrations/alembic.ini"
    """Path to the alembic.ini configuration file."""
    migration_path: Path = PACKAGE_ROOT / "db/migrations"
    """Path to the alembic migrations directory."""
    migration_ddl_version_table: str = "ddl_version"
    """Table name used by alembic to track migration versions."""
    fixture_path: Path = PACKAGE_ROOT / "db/fixtures"
    """Path to JSON fixture files for seeding tables."""

    @model_validator(mode="after")
    def _derive_url_from_database_env(self) -> DatabaseSettings:
        """Build `url` from `DATABASE_*` env vars when `CERT_RA_DB_URL` is unset.

        Fargate task definitions inject `DATABASE_HOST` / `DATABASE_PORT`
        (env) and `DATABASE_USER` / `DATABASE_PASSWORD` (ECS Secrets),
        but never compose a full URL. Doing it here keeps the same image
        usable in cloud without an entrypoint wrapper script.
        """
        if os.environ.get("CERT_RA_DB_URL"):
            return self
        host = os.environ.get("DATABASE_HOST")
        user = os.environ.get("DATABASE_USER")
        password = os.environ.get("DATABASE_PASSWORD")
        if not (host and user and password):
            return self
        port = os.environ.get("DATABASE_PORT", "5432")
        db_name = os.environ.get("DATABASE_NAME", "cert_ra")
        self.url = (
            f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}/{db_name}"
        )
        return self

    @model_validator(mode="after")
    def _validate_ssl_mode(self) -> DatabaseSettings:
        mode = self.ssl_mode.lower()
        if mode not in _VALID_SSL_MODES:
            msg = (
                f"Invalid ssl_mode {self.ssl_mode!r}; "
                f"expected one of {sorted(_VALID_SSL_MODES)}"
            )
            raise ValueError(msg)
        if mode in {"verify-ca", "verify-full"} and self.ssl_ca_path is None:
            msg = f"ssl_mode={mode!r} requires CERT_RA_DB_SSL_CA_PATH to be set"
            raise ValueError(msg)
        return self

    def build_ssl_param(self) -> ssl.SSLContext | bool:
        """Return an asyncpg-compatible `ssl` connect param.

        `False` disables TLS entirely. An `ssl.SSLContext` enables TLS
        with the verification posture matching `ssl_mode`.
        """
        mode = self.ssl_mode.lower()
        if mode == "disable":
            return False
        if mode == "require":
            # Encrypt but skip cert verification — equivalent to libpq's
            # sslmode=require. Matches RDS's force_ssl posture without
            # shipping a CA bundle, and accepts the self-signed cert that
            # docker-compose generates for local Postgres.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx
        # verify-ca / verify-full: ssl_ca_path is guaranteed non-None by
        # the model validator above.
        assert self.ssl_ca_path is not None
        ctx = ssl.create_default_context(cafile=str(self.ssl_ca_path))
        ctx.check_hostname = mode == "verify-full"
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx


class StorageSettings(BaseSettings):
    """File storage settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_storage_", case_sensitive=False, extra="ignore"
    )

    backend: str = "local"
    """Storage backend: 'local', 's3', 'gcs', or 'azure'."""
    upload_dir: Path = Path("uploads")
    """Upload directory for local backend."""
    bucket: str = ""
    """Cloud storage bucket name."""
    signed_url_expiry: int = 3600
    """Signed URL expiry time in seconds."""
    aws_access_key_id: str = ""
    """AWS access key ID."""
    aws_secret_access_key: str = ""
    """AWS secret access key."""
    aws_region: str = "us-east-1"
    """AWS region."""
    aws_endpoint: str = ""
    """Custom S3 endpoint URL (for MinIO, etc.)."""
    google_service_account: str = ""
    """Path to GCS service account JSON file."""
    azure_connection_string: str = ""
    """Azure storage connection string."""
    max_avatar_size: int = 5 * 1024 * 1024
    """Maximum avatar file size in bytes."""
    allowed_avatar_types: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    )
    """Allowed MIME types for avatar uploads."""
    max_report_size: int = 50 * 1024 * 1024
    """Maximum security report file size in bytes (50 MB)."""
    allowed_report_types: tuple[str, ...] = ("application/pdf",)
    """Allowed MIME types for security report uploads."""

    @property
    def is_cloud_storage(self) -> bool:
        """True if using a cloud storage backend."""
        return self.backend in {"s3", "gcs", "azure"}


@cache
def get_db_settings() -> DatabaseSettings:
    """Get database settings instance."""
    return DatabaseSettings()


@cache
def get_storage_settings() -> StorageSettings:
    """Get cached StorageSettings instance."""
    return StorageSettings()
