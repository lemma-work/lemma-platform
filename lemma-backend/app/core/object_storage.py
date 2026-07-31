"""Object storage factory helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from obstore.store import AzureStore, GCSStore, LocalStore, ObjectStore, S3Store

from app.core.config import settings

StorageBackend = Literal["local", "gcs", "s3", "azure"]


def _normalized_prefix(*parts: str | None) -> str | None:
    segments = [part.strip("/") for part in parts if part and part.strip("/")]
    return "/".join(segments) or None


def _gcs_store(*, bucket: str, prefix: str | None) -> GCSStore:
    return GCSStore(bucket=bucket, prefix=prefix)


def _s3_store(*, bucket: str, prefix: str | None) -> S3Store:
    return S3Store(bucket=bucket, prefix=prefix)


def _azure_store(*, container: str, prefix: str | None) -> AzureStore:
    return AzureStore(container_name=container, prefix=prefix)


def build_object_store(
    *,
    local_prefix: str | Path,
    bucket_name: str | None = None,
    force_backend: StorageBackend | None = None,
    remote_prefix: str | None = None,
) -> ObjectStore:
    backend = force_backend or settings.effective_storage_backend()
    prefix = _normalized_prefix(remote_prefix)
    if backend == "gcs":
        bucket = bucket_name or settings.storage_bucket
        if not bucket:
            raise ValueError("GCS storage backend requires STORAGE_BUCKET")
        return _gcs_store(bucket=bucket, prefix=prefix)
    if backend == "s3":
        bucket = bucket_name or settings.storage_bucket
        if not bucket:
            raise ValueError("S3 storage backend requires STORAGE_BUCKET")
        return _s3_store(bucket=bucket, prefix=prefix)
    if backend == "azure":
        container = bucket_name or settings.storage_bucket
        if not container:
            raise ValueError("Azure storage backend requires STORAGE_BUCKET")
        return _azure_store(container=container, prefix=prefix)
    if backend != "local":
        raise ValueError(f"Unsupported object storage backend: {backend}")

    return LocalStore(prefix=Path(local_prefix), mkdir=True)


def storage_supports_native_signed_urls() -> bool:
    return settings.effective_storage_backend() in {"gcs", "s3", "azure"}


def local_object_storage_path(*parts: str) -> Path:
    root = (
        settings.storage_bucket
        if settings.effective_storage_backend() == "local" and settings.storage_bucket
        else settings.local_object_storage_root
    )
    return Path(root).expanduser().joinpath(*parts)


def local_file_storage_path(*parts: str) -> Path:
    if settings.effective_storage_backend() == "local" and settings.storage_bucket:
        return Path(settings.storage_bucket).expanduser().joinpath("files", *parts)
    return Path(settings.local_file_storage_root).expanduser().joinpath(*parts)
