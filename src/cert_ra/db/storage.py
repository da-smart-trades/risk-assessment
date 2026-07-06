# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from advanced_alchemy.types.file_object import storages
from advanced_alchemy.types.file_object.backends.obstore import ObstoreBackend

from cert_ra.settings.db import get_storage_settings


def configure_storage() -> None:
    """Configure the file storage backend based on settings."""
    storage = get_storage_settings()
    backend = storage.backend

    if backend == "local":
        storage_path = storage.upload_dir.absolute()
        storage_path.mkdir(parents=True, exist_ok=True)
        avatars_backend = ObstoreBackend(key="avatars", fs=f"file://{storage_path}/")
        reports_backend = ObstoreBackend(key="reports", fs=f"file://{storage_path}/")
    elif backend == "s3":
        kwargs: dict[str, str] = {
            "key": "avatars",
            "fs": f"s3://{storage.bucket}/",
            "aws_region": storage.aws_region,
        }
        if storage.aws_access_key_id:
            kwargs["aws_access_key_id"] = storage.aws_access_key_id
            kwargs["aws_secret_access_key"] = storage.aws_secret_access_key
        if storage.aws_endpoint:
            kwargs["aws_endpoint"] = storage.aws_endpoint
        avatars_backend = ObstoreBackend(**kwargs)
        reports_backend = ObstoreBackend(**{**kwargs, "key": "reports"})
    elif backend == "gcs":
        kwargs = {"key": "avatars", "fs": f"gs://{storage.bucket}/"}
        if storage.google_service_account:
            kwargs["google_service_account"] = storage.google_service_account
        avatars_backend = ObstoreBackend(**kwargs)
        reports_backend = ObstoreBackend(**{**kwargs, "key": "reports"})
    elif backend == "azure":
        avatars_backend = ObstoreBackend(
            key="avatars",
            fs=f"az://{storage.bucket}/",
            azure_storage_connection_string=storage.azure_connection_string,
        )
        reports_backend = ObstoreBackend(
            key="reports",
            fs=f"az://{storage.bucket}/",
            azure_storage_connection_string=storage.azure_connection_string,
        )
    else:
        msg = f"Unsupported storage backend: {backend}"
        raise ValueError(msg)

    storages.register_backend(avatars_backend)
    storages.register_backend(reports_backend)
