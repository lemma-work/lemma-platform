"""Regression tests for DB pool exhaustion.

The file-download endpoints resolve + authorize inside a short Unit of Work and
then read bytes from object storage *after* that UoW (and its pooled DB
connection) has closed. These tests pin that invariant: the storage-read methods
must touch the DB repository for nothing — otherwise a slow/large download would
hold a connection for the whole transfer and starve the pool.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.datastore.domain.errors import DatastoreObjectNotFoundError
from app.modules.datastore.infrastructure.storage_paths import (
    CHILD_MARKDOWN_ARTIFACT,
    build_datastore_child_artifact_key,
    build_datastore_child_manifest_key,
)
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.modules.datastore.services.files.reader import FileReader


class _ExplodingRepo:
    """Any attribute access fails the test — proving the read path needs no DB."""

    def __getattr__(self, name):
        raise AssertionError(
            f"DB repository accessed during a session-free read: .{name}"
        )


class _DictStorage:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    async def download_file(self, key: str) -> bytes:
        if key in self.blobs:
            return self.blobs[key]
        raise DatastoreObjectNotFoundError(key)


def _reader(storage, *, skill_content: bytes | None = None) -> FileReader:
    system_skill_files = SimpleNamespace(read_file=lambda path: skill_content)
    # The other collaborators are only used during resolution
    # (get_file_by_path), which the session-free read methods never call.
    unused = SimpleNamespace()
    return FileReader(
        _ExplodingRepo(),
        storage,
        system_skill_files,
        unused,  # authz
        unused,  # authorizer
        unused,  # path_resolver
        unused,  # lookup
        unused,  # skills_overlay
    )


def _file_entity() -> SimpleNamespace:
    return SimpleNamespace(
        pod_id=uuid4(),
        path="/pod/report.pdf",
        name="report.pdf",
        is_folder=False,
    )


@pytest.mark.asyncio
async def test_read_content_for_entity_touches_no_repository():
    entity = _file_entity()
    storage = _DictStorage()
    storage.blobs[datastore_storage_key(entity)] = b"FILEBYTES"

    content = await _reader(storage).read_content_for_entity(entity)

    assert content == b"FILEBYTES"


@pytest.mark.asyncio
async def test_read_child_artifact_touches_no_repository():
    entity = _file_entity()
    storage = _DictStorage()
    manifest = {
        "artifacts": [
            {"name": CHILD_MARKDOWN_ARTIFACT, "content_type": "text/markdown"}
        ]
    }
    storage.blobs[build_datastore_child_manifest_key(entity.pod_id, entity.path)] = (
        json.dumps(manifest).encode("utf-8")
    )
    storage.blobs[
        build_datastore_child_artifact_key(
            entity.pod_id, entity.path, CHILD_MARKDOWN_ARTIFACT
        )
    ] = b"# Title\n"

    name, content, content_type = await _reader(storage).read_child_artifact(
        entity, CHILD_MARKDOWN_ARTIFACT
    )

    assert content == b"# Title\n"
    assert content_type == "text/markdown"
