"""Object storage factory helpers.

Stores are shared per process, not built per call.

A remote store's constructor resolves credentials before it returns, which on
GKE means a synchronous round trip to the metadata server: measured at 350-500ms
in production, every time, because nothing cached the result. That is not a cost
you can pay from a DI builder — the builders run on the event loop, so the whole
loop stops for it, and the loop-stall sampler caught exactly that, naming
``_gcs_store`` in more stall traces than any other frame.

Sharing them is safe. A store is immutable configuration plus an HTTP client;
it holds no per-request state, and the client underneath is the thing you want
reused anyway so connections survive between calls. This is the same shape as
``app/core/net/http_client.py`` and ``app/core/infrastructure/redis/client.py``:
a dict keyed on everything that participates in the store's identity, an
accessor that fills it on first use, and a reset for tests.

The cache is keyed rather than a single slot because one process legitimately
talks to several: the datastore bucket, the public icon bucket, and a local
path in tests. Keying on the resolved backend/bucket/prefix means a test that
monkeypatches ``settings`` gets a different key and therefore a different store,
without needing to remember to reset anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from obstore.store import AzureStore, GCSStore, LocalStore, ObjectStore, S3Store

from app.core.config import settings

StorageBackend = Literal["local", "gcs", "s3", "azure"]

# (backend, bucket-or-container-or-local-path, remote prefix) -> store.
_stores: dict[tuple[str, str, str | None], ObjectStore] = {}


def reset_object_stores() -> None:
    """Drop every cached store.

    For tests that point a backend at a temporary directory and then remove it:
    the key would still match on the next run and hand back a store rooted at a
    path that no longer exists.
    """
    _stores.clear()


def _normalized_prefix(*parts: str | None) -> str | None:
    segments = [part.strip("/") for part in parts if part and part.strip("/")]
    return "/".join(segments) or None


def _gcs_store(*, bucket: str, prefix: str | None) -> GCSStore:
    return GCSStore(bucket=bucket, prefix=prefix)


def _s3_store(*, bucket: str, prefix: str | None) -> S3Store:
    """The S3 store, optionally against an S3-compatible endpoint.

    With no endpoint configured this is AWS S3 and obstore resolves the region
    endpoint itself. ``STORAGE_ENDPOINT_URL`` points it somewhere else — MinIO,
    R2, Wasabi — which also gives the e2e suite a real multipart implementation
    to test against. That matters here specifically: part-size rules are
    enforced by the server, so a local filesystem store cannot catch a chunk
    size the real API would reject, and one did reach production.

    Path-style addressing because a self-hosted endpoint rarely has per-bucket
    DNS, and plain HTTP is allowed only for an explicitly ``http://`` endpoint.
    """
    endpoint = (settings.storage_endpoint_url or "").strip()
    if not endpoint:
        return S3Store(bucket=bucket, prefix=prefix)
    return S3Store(
        bucket=bucket,
        prefix=prefix,
        endpoint=endpoint,
        virtual_hosted_style_request=False,
        client_options={"allow_http": endpoint.startswith("http://")},
    )


def _azure_store(*, container: str, prefix: str | None) -> AzureStore:
    return AzureStore(container_name=container, prefix=prefix)


def build_object_store(
    *,
    local_prefix: str | Path,
    bucket_name: str | None = None,
    force_backend: StorageBackend | None = None,
    remote_prefix: str | None = None,
) -> ObjectStore:
    """Return the shared store for these coordinates, creating it on first use.

    Callers keep calling this per request; the construction happens once. Errors
    for a missing bucket are raised before the cache is consulted so a
    misconfigured deployment fails the same way on the first call and the
    thousandth.
    """
    backend = force_backend or settings.effective_storage_backend()
    prefix = _normalized_prefix(remote_prefix)

    if backend in {"gcs", "s3", "azure"}:
        bucket = bucket_name or settings.storage_bucket
        if not bucket:
            raise ValueError(
                f"{backend.upper()} storage backend requires STORAGE_BUCKET"
            )
        target = bucket
    elif backend == "local":
        target = str(Path(local_prefix))
    else:
        raise ValueError(f"Unsupported object storage backend: {backend}")

    key = (backend, target, prefix)
    store = _stores.get(key)
    if store is None:
        store = _construct_object_store(backend, target, prefix)
        _stores[key] = store
    return store


def _construct_object_store(
    backend: str, target: str, prefix: str | None
) -> ObjectStore:
    if backend == "gcs":
        return _gcs_store(bucket=target, prefix=prefix)
    if backend == "s3":
        return _s3_store(bucket=target, prefix=prefix)
    if backend == "azure":
        return _azure_store(container=target, prefix=prefix)
    return LocalStore(prefix=Path(target), mkdir=True)


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
