"""Regression: the app storage phase touches no DB repository.

``AppStoragePhase`` is constructed with only the storage factory — it has no
repository attribute at all — so the asset/archive/bundle/delete storage sagas
are DB-free by construction and can run after the resolving UoW has closed.
"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.modules.apps.services.app_storage_phase import (
    AppStoragePhase,
    _AppDeletionCleanup,
    _AssetReadInputs,
    _UploadPlan,
    _WrittenBundle,
)
from app.modules.apps.domain.entities import AppReleaseEntity


class _RecordingStorage:
    def __init__(self, blobs: dict[str, bytes] | None = None):
        self.blobs: dict[str, bytes] = dict(blobs or {})
        self.deleted: list[str] = []
        self.deleted_prefixes: list[str] = []

    async def read_file(self, key: str) -> bytes:
        if key in self.blobs:
            return self.blobs[key]
        raise FileNotFoundError(key)

    async def write_file(self, key: str, content: bytes) -> None:
        self.blobs[key] = content

    async def delete_file(self, key: str) -> None:
        self.deleted.append(key)

    async def delete_prefix(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)


def _phase(storage):
    return AppStoragePhase(lambda app_id: storage)


def test_app_storage_phase_holds_no_repository():
    sp = _phase(_RecordingStorage())
    assert not hasattr(sp, "repository")
    assert not hasattr(sp, "file_repository")


@pytest.mark.asyncio
async def test_read_archive_without_db():
    storage = _RecordingStorage({"releases/v1/archive.zip": b"ZIPBYTES"})
    content = await _phase(storage).read_archive(uuid4(), "releases/v1/archive.zip")
    assert content == b"ZIPBYTES"


@pytest.mark.asyncio
async def test_read_asset_serves_index_without_db():
    storage = _RecordingStorage({"releases/v1/dist/index.html": b"<html>"})
    inputs = _AssetReadInputs(
        app_id=uuid4(),
        pod_id=uuid4(),
        dist_root_path="releases/v1/dist/",
        normalized_asset_path="",
        quoted_etag='"v1"',
    )
    doc = await _phase(storage).read_asset(inputs)
    assert doc.is_entrypoint is True
    assert doc.etag == '"v1"'


@pytest.mark.asyncio
async def test_cleanup_storage_purges_without_db():
    storage = _RecordingStorage()
    cleanup = _AppDeletionCleanup(
        app_id=uuid4(),
        source_archive_path="source/archive.zip",
        releases=(),
    )
    await _phase(storage).cleanup_storage(cleanup)
    assert "source/archive.zip" in storage.deleted
    assert "" in storage.deleted_prefixes


@pytest.mark.asyncio
async def test_cleanup_written_bundle_removes_source_and_release_prefix():
    storage = _RecordingStorage()
    plan = _UploadPlan(
        app_id=uuid4(),
        pod_id=uuid4(),
        name="dashboard",
        has_source=True,
        version="v1",
        release_root="releases/v1/dist/",
        existing_release_id=None,
        needs_dist_write=True,
    )

    await _phase(storage).cleanup_written_bundle(
        plan,
        _WrittenBundle(
            source_path="source/hash/archive.zip",
            dist_archive_path="releases/v1/dist/archive.zip",
        ),
    )

    assert storage.deleted == ["source/hash/archive.zip"]
    assert storage.deleted_prefixes == ["releases/v1/dist/"]


@pytest.mark.asyncio
async def test_bundle_write_failure_rolls_back_every_partial_object():
    class _FailingStorage(_RecordingStorage):
        async def write_file(self, key: str, content) -> None:
            if key.endswith("archive.zip") and key.startswith("releases/"):
                raise OSError("object storage write failed")
            await super().write_file(key, content)

    def archive(files: dict[str, str]) -> bytes:
        buffer = BytesIO()
        with ZipFile(buffer, "w") as output:
            for path, content in files.items():
                output.writestr(path, content)
        return buffer.getvalue()

    storage = _FailingStorage()
    plan = _UploadPlan(
        app_id=uuid4(),
        pod_id=uuid4(),
        name="dashboard",
        has_source=True,
        version="v1",
        release_root="releases/v1/dist/",
        existing_release_id=None,
        needs_dist_write=True,
    )

    with pytest.raises(OSError, match="object storage write failed"):
        await _phase(storage).write_bundle(
            plan,
            archive({"src/main.ts": "export {}"}),
            archive({"index.html": "<html></html>"}),
        )

    assert len(storage.deleted) == 1
    assert storage.deleted[0].startswith("source/")
    assert storage.deleted_prefixes == ["releases/v1/dist/"]


@pytest.mark.asyncio
async def test_cleanup_storage_deletes_release_archive_outside_dist_prefix():
    storage = _RecordingStorage()
    release = AppReleaseEntity(
        app_id=uuid4(),
        version="v1",
        dist_root_path="releases/v1/dist/",
        dist_archive_path="releases/v1/archive.zip",
    )
    cleanup = _AppDeletionCleanup(
        app_id=release.app_id,
        source_archive_path=None,
        releases=(release,),
    )

    await _phase(storage).cleanup_storage(cleanup)

    assert storage.deleted_prefixes == ["releases/v1/dist/", ""]
    assert storage.deleted == ["releases/v1/archive.zip"]
