"""Controller-level regression for DB pool exhaustion.

The streaming download endpoint must resolve + authorize the file *inside* a
short Unit of Work and read the bytes from storage *after* that UoW (and its
pooled DB connection) has been released — otherwise a slow/large transfer pins a
connection for its whole duration. These tests drive the endpoint function
directly with a tracking ``uow_factory`` to pin that ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.api.controllers import file_controller


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
async def test_download_file_resolves_in_uow_then_reads_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    entity = SimpleNamespace(content_type="text/plain", name="notes.txt")
    service = _FakeFileService(factory.state, b"DOWNLOAD-BYTES", entity)

    monkeypatch.setattr(file_controller, "build_file_service", lambda uow: service)
    monkeypatch.setattr(
        file_controller, "_to_file_response", lambda e, uid: SimpleNamespace(pod_id=None)
    )
    monkeypatch.setattr(file_controller, "_ensure_file_in_pod", lambda r, pid: None)
    monkeypatch.setattr(
        file_controller, "resolve_pod_context", AsyncMock(return_value=object())
    )

    response = await file_controller.download_file(
        uuid4(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(),  # request
        path="/notes.txt",
        uow_factory=factory,
    )

    # Resolve + authorize happened while the connection was held...
    assert service.resolved_while_open is True
    # ...but the storage read happened only after the UoW closed.
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert factory.state["opens"] == 1

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"DOWNLOAD-BYTES"
    assert "filename" in response.headers.get("content-disposition", "")


class _FakeChildService:
    def __init__(self, state, content):
        self._state = state
        self._content = content
        self.resolved_while_open = None
        self.read_while_open = None

    async def resolve_child(self, pod_id, path, ctx):
        self.resolved_while_open = self._state["open"]
        return SimpleNamespace(content_type="text/markdown", name="report.pdf"), "doc.md"

    async def read_child_content(self, file_entity, artifact_rel, *, page_start, page_end):
        self.read_while_open = self._state["open"]
        return "doc.md", self._content, "text/markdown"


@pytest.mark.asyncio
async def test_download_file_child_resolves_in_uow_then_reads_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    service = _FakeChildService(factory.state, b"# child")

    monkeypatch.setattr(file_controller, "build_file_service", lambda uow: service)
    monkeypatch.setattr(
        file_controller, "_to_file_response", lambda e, uid: SimpleNamespace(pod_id=None)
    )
    monkeypatch.setattr(file_controller, "_ensure_file_in_pod", lambda r, pid: None)
    monkeypatch.setattr(
        file_controller, "resolve_pod_context", AsyncMock(return_value=object())
    )

    response = await file_controller.download_file_child(
        uuid4(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(),  # request
        path="/report.pdf/doc.md",
        page_start=None,
        page_end=None,
        uow_factory=factory,
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"# child"


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
        return [
            {
                "name": "doc.md",
                "path": "/report.pdf/doc.md",
                "kind": "artifact",
                "content_type": "text/markdown",
                "size_bytes": 1,
                "page_number": None,
            }
        ]


@pytest.mark.asyncio
async def test_list_children_resolves_in_uow_then_reads_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    service = _FakeChildrenService(factory.state)

    monkeypatch.setattr(file_controller, "build_file_service", lambda uow: service)
    monkeypatch.setattr(
        file_controller,
        "_to_file_response",
        lambda e, uid: SimpleNamespace(path="/report.pdf", pod_id=None),
    )
    monkeypatch.setattr(file_controller, "_ensure_file_in_pod", lambda r, pid: None)
    monkeypatch.setattr(
        file_controller, "resolve_pod_context", AsyncMock(return_value=object())
    )

    response = await file_controller.list_file_children(
        uuid4(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(),  # request
        path="/report.pdf",
        uow_factory=factory,
    )

    # Resolve happened under the connection; the storage manifest read did not.
    assert service.resolved_while_open is True
    assert service.load_while_open is False
    assert factory.state["open"] is False
    assert len(response.items) == 1


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

    monkeypatch.setattr(file_controller, "build_file_service", lambda uow: service)
    monkeypatch.setattr(
        file_controller, "resolve_pod_context", AsyncMock(return_value=object())
    )
    # e2e-style: no datastore worker, so the offload is skipped and cleanup runs
    # in-process — but still only after the UoW (connection) has been released.
    monkeypatch.setattr(
        file_controller.settings, "e2e_disable_worker_file_autoindex", True
    )
    enqueue_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        file_controller, "enqueue_datastore_path_cleanup", enqueue_mock
    )

    response = await file_controller.delete_path(
        uuid4(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(),  # request
        path="/x.txt",
        uow_factory=factory,
    )

    assert response.status_code == 204
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

    monkeypatch.setattr(file_controller, "build_file_service", lambda uow: service)
    monkeypatch.setattr(
        file_controller, "resolve_pod_context", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        file_controller.settings, "e2e_disable_worker_file_autoindex", False
    )
    monkeypatch.setattr(file_controller, "enqueue_datastore_path_cleanup", _enqueue)

    response = await file_controller.delete_path(
        uuid4(),
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(),  # request
        path="/folder",
        uow_factory=factory,
    )

    assert response.status_code == 204
    assert service.resolved_while_open is True
    # The cleanup was offloaded (not run in-process) and enqueued only after the
    # connection was released.
    assert service.cleanup_called is False
    assert enqueue_open_state["open"] is False
    assert factory.state["open"] is False
