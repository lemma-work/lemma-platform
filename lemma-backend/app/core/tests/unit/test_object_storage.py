from __future__ import annotations

from pathlib import Path

import obstore as obs
import pytest
from obstore.store import LocalStore

from app.core import object_storage
from app.core.config import Settings, settings


def test_storage_settings_use_one_backend_and_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("STORAGE_BUCKET", "documents")
    monkeypatch.delenv("GCS_STORAGE_BUCKET", raising=False)

    configured = Settings(_env_file=None)

    assert configured.storage_backend == "s3"
    assert configured.storage_bucket == "documents"
    assert configured.effective_storage_backend() == "s3"
    assert "object_storage_prefix" not in Settings.model_fields


def test_legacy_gcs_bucket_environment_alias_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STORAGE_BUCKET", raising=False)
    monkeypatch.setenv("GCS_STORAGE_BUCKET", "legacy-documents")

    configured = Settings(
        environment="production",
        app_base_domain="apps.example.com",
        _env_file=None,
    )

    assert configured.storage_bucket == "legacy-documents"
    assert configured.effective_storage_backend() == "gcs"


@pytest.mark.parametrize(
    ("backend", "provider_name", "location_key"),
    [
        ("gcs", "GCSStore", "bucket"),
        ("s3", "S3Store", "bucket"),
        ("azure", "AzureStore", "container_name"),
    ],
)
def test_cloud_store_selection_uses_unified_bucket_and_internal_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: str,
    provider_name: str,
    location_key: str,
) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def provider(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(settings, "storage_backend", backend)
    monkeypatch.setattr(settings, "storage_bucket", "documents")
    monkeypatch.setattr(object_storage, provider_name, provider)

    store = object_storage.build_object_store(
        local_prefix=tmp_path,
        remote_prefix="/apps/example/",
    )

    assert store is sentinel
    assert captured == {location_key: "documents", "prefix": "apps/example"}


@pytest.mark.parametrize("backend", ["gcs", "s3", "azure"])
def test_cloud_store_requires_bucket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: str,
) -> None:
    monkeypatch.setattr(settings, "storage_backend", backend)
    monkeypatch.setattr(settings, "storage_bucket", None)

    with pytest.raises(ValueError, match="STORAGE_BUCKET"):
        object_storage.build_object_store(local_prefix=tmp_path)


@pytest.mark.asyncio
async def test_local_store_uses_storage_bucket_as_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_bucket", str(root))

    path = object_storage.local_object_storage_path("datastore")
    store = object_storage.build_object_store(local_prefix=path)

    assert isinstance(store, LocalStore)
    await obs.put_async(store, "health.txt", b"ok")
    assert (root / "datastore" / "health.txt").read_bytes() == b"ok"


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("local", False), ("gcs", True), ("s3", True), ("azure", True)],
)
def test_native_signed_url_capability(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(settings, "storage_backend", backend)

    assert object_storage.storage_supports_native_signed_urls() is expected
