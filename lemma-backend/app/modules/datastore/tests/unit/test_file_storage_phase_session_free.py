"""Regression: the file storage phase touches no DB repository.

``FileStoragePhase`` runs after the resolving Unit of Work has closed, so it must
hold no pooled DB connection. We build it with a projection backed by an
``_ExplodingRepo`` — any repository access raises — and exercise every method,
proving the update/delete storage sagas are DB-free by construction.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.datastore.domain.errors import DatastoreInfrastructureError
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import FileProjection
from app.modules.datastore.services.files.storage_phase import (
    FileStoragePhase,
    _StorageMove,
    _UpdatePlan,
)


class _ExplodingRepo:
    """Any attribute access fails the test — proving the storage phase needs no DB."""

    def __getattr__(self, name):
        raise AssertionError(f"DB repository accessed in the storage phase: .{name}")


class _RecordingStorage:
    def __init__(self):
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.deleted_prefixes: list[str] = []

    async def upload_file(self, key: str, content: bytes) -> None:
        self.uploaded[key] = content

    async def download_file(self, key: str) -> bytes:
        return self.uploaded.get(key, b"OLD-CONTENT")

    async def stat_file(self, key: str) -> int:
        return len(self.uploaded[key])

    async def copy_file(self, source: str, destination: str) -> None:
        self.uploaded[destination] = self.uploaded.get(source, b"OLD-CONTENT")

    async def delete_file(self, key: str) -> None:
        self.deleted.append(key)

    async def delete_prefix(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)


class _FakeSearch:
    def __init__(self):
        self.removed: list = []
        self.path_updates: list = []

    async def remove_file(self, file_id) -> None:
        self.removed.append(file_id)

    async def update_file_path(self, file_id, path, parent) -> None:
        self.path_updates.append((file_id, path, parent))


def _storage_phase(storage, search):
    # Projection backed by an exploding repo: the storage phase must only call its
    # repo-free methods (storage_key / delete_child_artifacts).
    projection = FileProjection(storage, _ExplodingRepo())
    return FileStoragePhase(
        storage, lambda: lambda pod_id: search, projection, PathResolver()
    )


def _file_entity(path: str, file_id):
    return SimpleNamespace(
        pod_id=uuid4(),
        path=path,
        name=path.rsplit("/", 1)[-1],
        is_file=True,
        is_folder=False,
        search_enabled=True,
        mime_type="text/plain",
        id=file_id,
        size_bytes=3,
    )


def test_storage_phase_holds_no_repository():
    sp = _storage_phase(_RecordingStorage(), _FakeSearch())
    assert not hasattr(sp, "file_repository")
    assert not hasattr(sp, "repository")


@pytest.mark.asyncio
async def test_write_update_uploads_new_content_without_db():
    storage = _RecordingStorage()
    sp = _storage_phase(storage, _FakeSearch())
    entity = _file_entity("/p/f.txt", uuid4())
    plan = _UpdatePlan(
        file_entity=entity,
        previous_path="/p/f.txt",
        previous_search_enabled=True,
        previous_storage_key=None,
        new_storage_key="pod/f.txt",
        has_content=True,
        rename_moved=False,
        storage_moves=(),
        should_sync=True,
        requester_user_id=uuid4(),
    )
    await sp.write_update(plan, SimpleNamespace(content=b"NEW"))
    assert storage.uploaded["pod/f.txt"] == b"NEW"


@pytest.mark.asyncio
async def test_failed_update_persistence_cleans_only_new_blob_without_db():
    storage = _RecordingStorage()
    sp = _storage_phase(storage, _FakeSearch())
    entity = _file_entity("/p/f.txt", uuid4())
    plan = _UpdatePlan(
        file_entity=entity,
        previous_path="/p/f.txt",
        previous_search_enabled=True,
        previous_storage_key="pod/revisions/1",
        new_storage_key="pod/revisions/2",
        has_content=True,
        rename_moved=False,
        storage_moves=(),
        should_sync=True,
        requester_user_id=uuid4(),
    )

    await sp.cleanup_uncommitted_update(plan)

    assert storage.deleted == ["pod/revisions/2"]


@pytest.mark.asyncio
async def test_finalize_update_after_rename_deletes_old_blob_without_db():
    storage = _RecordingStorage()
    search = _FakeSearch()
    sp = _storage_phase(storage, search)
    updated = _file_entity("/p/new.txt", uuid4())
    plan = _UpdatePlan(
        file_entity=updated,
        previous_path="/p/old.txt",
        previous_search_enabled=True,
        previous_storage_key="pod/old.txt",
        new_storage_key="pod/new.txt",
        has_content=False,
        rename_moved=True,
        storage_moves=(_StorageMove("pod/old.txt", "pod/new.txt"),),
        should_sync=True,
        requester_user_id=uuid4(),
    )
    await sp.finalize_update(plan, updated)
    # Old blob deleted only after the row was (notionally) persisted.
    assert "pod/old.txt" in storage.deleted


@pytest.mark.asyncio
async def test_partial_folder_move_rolls_back_staged_destinations():
    storage = _RecordingStorage()
    calls = 0

    async def _copy(source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DatastoreInfrastructureError("copy failed")
        storage.uploaded[destination] = b"COPIED"

    storage.copy_file = _copy
    sp = _storage_phase(storage, _FakeSearch())
    entity = _file_entity("/new", uuid4())
    plan = _UpdatePlan(
        file_entity=entity,
        previous_path="/old",
        previous_search_enabled=True,
        previous_storage_key=None,
        new_storage_key=None,
        has_content=False,
        rename_moved=True,
        storage_moves=(
            _StorageMove("pod/old/a", "pod/new/a"),
            _StorageMove("pod/old/b", "pod/new/b"),
        ),
        should_sync=True,
        requester_user_id=uuid4(),
    )

    with pytest.raises(DatastoreInfrastructureError, match="move file content"):
        await sp.write_update(plan, SimpleNamespace(content=None))

    assert storage.deleted == ["pod/new/a"]


@pytest.mark.asyncio
async def test_finalize_same_path_content_update_keeps_canonical_object():
    storage = _RecordingStorage()
    sp = _storage_phase(storage, _FakeSearch())
    updated = _file_entity("/p/f.txt", uuid4())
    plan = _UpdatePlan(
        file_entity=updated,
        previous_path=updated.path,
        previous_search_enabled=True,
        previous_storage_key="pod/f.txt",
        new_storage_key="pod/f.txt",
        has_content=True,
        rename_moved=False,
        storage_moves=(),
        should_sync=True,
        requester_user_id=uuid4(),
    )

    await sp.finalize_update(plan, updated)

    assert storage.deleted == []


@pytest.mark.asyncio
async def test_cleanup_deleted_paths_purges_storage_and_index_without_db():
    storage = _RecordingStorage()
    search = _FakeSearch()
    sp = _storage_phase(storage, search)
    file_id = uuid4()
    await sp.cleanup_deleted_paths(
        uuid4(),
        is_folder=False,
        folder_prefix=None,
        files=[
            {"file_id": str(file_id), "path": "/p/f.txt", "storage_key": "pod/f.txt"}
        ],
    )
    assert "pod/f.txt" in storage.deleted
    assert file_id in search.removed
