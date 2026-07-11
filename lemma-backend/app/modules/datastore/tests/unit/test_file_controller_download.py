"""Use-case-level regression for DB pool exhaustion.

The file sagas must resolve + authorize *inside* a short Unit of Work and do the
storage / search work *after* that UoW (and its pooled DB connection) has been
released — otherwise a slow/large transfer or a many-object purge pins a
connection for its whole duration. These tests drive ``FileUseCases`` (the owner
of the saga) with a tracking ``uow_factory`` to pin that ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.application import file_use_cases as ucmod
from app.modules.datastore.application.file_use_cases import FileUseCases


class _TrackingUowFactory:
    """A ``uow_factory`` whose context manager flips a shared ``open`` flag, so a
    test can observe whether the connection is held during a given call."""

    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_, *exc):
                state["open"] = False
                return False

        return _Cm()


def _use_cases(factory, service):
    """Build FileUseCases with a stubbed pod-context resolution + a fixed
    per-phase service. ``resolve_pod_context`` is patched globally per test."""
    return FileUseCases(factory, lambda uow: service)


@pytest.fixture(autouse=True)
def _stub_pod_context(monkeypatch):
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=object()),
    )


class _FakeFileService:
    def __init__(self, state, content, entity):
        self._state = state
        self._content = content
        self._entity = entity
        self.resolved_while_open = None
        self.read_while_open = None

    async def resolve_readable_file(self, pod_id, path, ctx):
        self.resolved_while_open = self._state["open"]
        return self._entity

    async def read_file_content(self, entity):
        self.read_while_open = self._state["open"]
        return self._content


@pytest.mark.asyncio
async def test_download_file_resolves_in_uow_then_reads_after_release():
    factory = _TrackingUowFactory()
    entity = SimpleNamespace(content_type="text/plain", name="notes.txt")
    service = _FakeFileService(factory.state, b"DOWNLOAD-BYTES", entity)

    result = await _use_cases(factory, service).download_file(
        pod_id=uuid4(), path="/notes.txt", request=SimpleNamespace(), user_id=uuid4()
    )

    # Resolve + authorize happened while the connection was held...
    assert service.resolved_while_open is True
    # ...but the storage read happened only after the UoW closed.
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert factory.state["opens"] == 1
    assert result.content == b"DOWNLOAD-BYTES"
    assert result.entity is entity


@pytest.mark.asyncio
async def test_conditional_download_authorizes_but_skips_storage_read():
    factory = _TrackingUowFactory()
    digest = "a" * 64
    entity = SimpleNamespace(
        content_type="text/plain",
        name="notes.txt",
        content_sha256=digest,
    )
    service = _FakeFileService(factory.state, b"DOWNLOAD-BYTES", entity)

    result = await _use_cases(factory, service).download_file(
        pod_id=uuid4(),
        path="/notes.txt",
        request=SimpleNamespace(),
        user_id=uuid4(),
        if_none_match=f'W/"{digest}"',
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is None
    assert result.not_modified is True
    assert result.content is None


class _FakeChildService:
    def __init__(self, state, content):
        self._state = state
        self._content = content
        self.resolved_while_open = None
        self.read_while_open = None

    async def resolve_child(self, pod_id, path, ctx):
        self.resolved_while_open = self._state["open"]
        return SimpleNamespace(
            content_type="text/markdown", name="report.pdf"
        ), "doc.md"

    async def read_child_content(
        self, file_entity, artifact_rel, *, page_start, page_end
    ):
        self.read_while_open = self._state["open"]
        return "doc.md", self._content, "text/markdown"


@pytest.mark.asyncio
async def test_download_child_resolves_in_uow_then_reads_after_release():
    factory = _TrackingUowFactory()
    service = _FakeChildService(factory.state, b"# child")

    result = await _use_cases(factory, service).download_child(
        pod_id=uuid4(),
        path="/report.pdf/doc.md",
        request=SimpleNamespace(),
        user_id=uuid4(),
        page_start=None,
        page_end=None,
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert result.content == b"# child"
    assert result.content_type == "text/markdown"


class _FakeChildrenService:
    def __init__(self, state):
        self._state = state
        self.resolved_while_open = None
        self.load_while_open = None

    async def resolve_children_file(self, pod_id, path, ctx):
        self.resolved_while_open = self._state["open"]
        return SimpleNamespace(path="/report.pdf", pod_id=None)

    async def load_file_children(self, file_entity, requester_user_id):
        self.load_while_open = self._state["open"]
        return [{"name": "doc.md", "path": "/report.pdf/doc.md"}]


@pytest.mark.asyncio
async def test_list_children_resolves_in_uow_then_reads_after_release():
    factory = _TrackingUowFactory()
    service = _FakeChildrenService(factory.state)

    result = await _use_cases(factory, service).list_children(
        pod_id=uuid4(), path="/report.pdf", request=SimpleNamespace(), user_id=uuid4()
    )

    # Resolve happened under the connection; the storage manifest read did not.
    assert service.resolved_while_open is True
    assert service.load_while_open is False
    assert factory.state["open"] is False
    assert len(result.children) == 1


class _FakeDeleteService:
    def __init__(self, state, cleanup):
        self._state = state
        self._cleanup = cleanup
        self.resolved_while_open = None
        self.cleanup_while_open = None
        self.cleanup_called = False

    async def resolve_delete_path(self, pod_id, path, ctx):
        self.resolved_while_open = self._state["open"]
        return self._cleanup

    async def cleanup_deleted_paths(self, pod_id, *, is_folder, folder_prefix, files):
        self.cleanup_called = True
        self.cleanup_while_open = self._state["open"]


@pytest.mark.asyncio
async def test_delete_path_in_process_cleanup_runs_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    cleanup = SimpleNamespace(
        pod_id=uuid4(), is_folder=False, folder_prefix=None, files=()
    )
    service = _FakeDeleteService(factory.state, cleanup)

    # e2e-style: no datastore worker, so the offload is skipped and cleanup runs
    # in-process — but still only after the UoW (connection) has been released.
    monkeypatch.setattr(ucmod.settings, "e2e_disable_worker_file_autoindex", True)
    enqueue_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(ucmod, "enqueue_datastore_path_cleanup", enqueue_mock)

    await _use_cases(factory, service).delete_path(
        pod_id=uuid4(), path="/x.txt", request=SimpleNamespace(), user_id=uuid4()
    )

    assert service.resolved_while_open is True
    enqueue_mock.assert_not_awaited()
    assert service.cleanup_called is True
    assert service.cleanup_while_open is False
    assert factory.state["open"] is False


@pytest.mark.asyncio
async def test_delete_path_offloads_cleanup_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    cleanup = SimpleNamespace(
        pod_id=uuid4(),
        is_folder=True,
        folder_prefix="pods/x/folder/",
        files=(),
    )
    service = _FakeDeleteService(factory.state, cleanup)
    enqueue_open_state = {}

    async def _enqueue(**kwargs):
        enqueue_open_state["open"] = factory.state["open"]
        return True

    monkeypatch.setattr(ucmod.settings, "e2e_disable_worker_file_autoindex", False)
    monkeypatch.setattr(ucmod, "enqueue_datastore_path_cleanup", _enqueue)

    await _use_cases(factory, service).delete_path(
        pod_id=uuid4(), path="/folder", request=SimpleNamespace(), user_id=uuid4()
    )

    assert service.resolved_while_open is True
    # The cleanup was offloaded (not run in-process) and enqueued only after the
    # connection was released.
    assert service.cleanup_called is False
    assert enqueue_open_state["open"] is False
    assert factory.state["open"] is False


class _FakeUpdateService:
    def __init__(self, state):
        self._state = state
        self.resolve_open = None
        self.write_open = None
        self.persist_open = None
        self.getfile_open = None
        self.finalize_open = None

    async def resolve_update_file(self, pod_id, update_entity, ctx):
        self.resolve_open = self._state["open"]
        return SimpleNamespace(id=uuid4())

    async def write_update_storage(self, plan, update_entity):
        self.write_open = self._state["open"]

    async def persist_update_file(self, plan):
        self.persist_open = self._state["open"]
        return SimpleNamespace(id=uuid4())

    async def get_file(self, file_id, ctx):
        self.getfile_open = self._state["open"]
        return SimpleNamespace(id=file_id, pod_id=None)

    async def finalize_update_file(self, plan, updated):
        self.finalize_open = self._state["open"]


@pytest.mark.asyncio
async def test_update_file_moves_bytes_and_finalizes_outside_uow():
    factory = _TrackingUowFactory()
    service = _FakeUpdateService(factory.state)

    await _use_cases(factory, service).update_file(
        pod_id=uuid4(),
        update_entity=SimpleNamespace(),
        request=SimpleNamespace(),
        user_id=uuid4(),
    )

    assert service.resolve_open is True  # UoW#1: resolve + authorize + mutate
    assert service.write_open is False  # bytes moved with no connection held
    assert service.persist_open is True  # UoW#2: persist the row
    assert service.getfile_open is True  # response re-read in UoW#2
    assert service.finalize_open is False  # storage + search sync after commit
    assert factory.state["open"] is False
    assert factory.state["opens"] == 2  # exactly two short UoWs
