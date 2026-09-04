from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from sandbox_runtime.errors import SandboxPathNotFound, SandboxUnavailable

from app.modules.workspace.api.controllers import files_controller as controller
from app.modules.workspace.providers.runtime_client import (
    WorkspaceRuntimeFileNotFound,
    WorkspaceRuntimeFileRejected,
)


def _stat(path: str, kind: str = "file", size: int = 12) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        kind=kind,
        size_bytes=size,
        modified_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )


# --- path clamping ----------------------------------------------------------


def test_a_relative_path_resolves_under_the_workspace_root() -> None:
    assert controller._workspace_path("notes/a.md") == "/workspace/notes/a.md"
    assert controller._workspace_path(None) == "/workspace"
    assert controller._workspace_path("") == "/workspace"


def test_the_workspace_root_itself_is_allowed() -> None:
    assert controller._workspace_path("/workspace") == "/workspace"


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/.git-credentials",
        "/tmp",
        "/etc/passwd",
        "../../etc/passwd",
        "/workspace/../tmp/secret",
        "/workspace/../../root",
    ],
)
def test_nothing_outside_the_workspace_is_readable(path: str) -> None:
    """`/tmp` is where the credential bridge stages secrets, so a viewer route
    that could read it would publish them to any request carrying the session."""
    with pytest.raises(HTTPException) as raised:
        controller._workspace_path(path)
    assert raised.value.status_code == 422


def test_a_traversal_that_lands_back_inside_is_allowed() -> None:
    """Refusing this would be a lie about what the path means."""
    assert controller._workspace_path("/workspace/a/../b.txt") == "/workspace/b.txt"


def test_a_null_byte_is_refused() -> None:
    with pytest.raises(HTTPException) as raised:
        controller._workspace_path("/workspace/a\x00b")
    assert raised.value.status_code == 422


# --- ambient listing --------------------------------------------------------


class _FakeSession:
    def __init__(self, stats):
        self._stats = stats
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def list_files(self, path):
        return self._stats

    async def stat_file(self, path):
        return self._stats[0]

    async def read_file(self, path, *, offset=0, length=None):
        return b"hello"


class _FakeService:
    def __init__(self, *, running: bool, stats=()):
        self.sandbox = SimpleNamespace(
            get_sandbox=self._get_sandbox,
        )
        self._running = running
        self.session = _FakeSession(list(stats))
        self.sessions_created = 0
        self.closed = False

    async def _get_sandbox(self, user_id):
        return SimpleNamespace(status="RUNNING") if self._running else None

    async def get_session(self, user_id, **kwargs):
        self.sessions_created += 1
        return self.session

    async def close(self):
        self.closed = True


def _user():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_listing_a_paused_workspace_does_not_start_it() -> None:
    """A pane that boots a sandbox on every render is a cost bug against a
    900-second idle release."""
    service = _FakeService(running=False)

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False
    )

    assert result.sleeping is True
    assert result.entries == []
    assert service.sessions_created == 0
    assert service.closed is True


@pytest.mark.asyncio
async def test_listing_wakes_the_workspace_when_asked() -> None:
    service = _FakeService(running=False, stats=[_stat("/workspace/a.md")])

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=True
    )

    assert result.sleeping is False
    assert [entry.name for entry in result.entries] == ["a.md"]
    assert service.sessions_created == 1


@pytest.mark.asyncio
async def test_a_running_workspace_is_listed_without_being_asked_to_wake() -> None:
    service = _FakeService(
        running=True,
        stats=[_stat("/workspace/src", kind="directory", size=0)],
    )

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False
    )

    assert result.sleeping is False
    assert result.entries[0].kind == "directory"
    assert result.entries[0].name == "src"


@pytest.mark.asyncio
async def test_a_directory_larger_than_one_page_says_so() -> None:
    stats = [
        _stat(f"/workspace/f{index}") for index in range(controller._MAX_ENTRIES + 5)
    ]
    service = _FakeService(running=True, stats=stats)

    result = await controller.list_workspace_files(
        _user(), service, path=None, wake=False
    )

    assert result.truncated is True
    assert len(result.entries) == controller._MAX_ENTRIES


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected",
    [
        (SandboxPathNotFound("gone"), 404),
        (WorkspaceRuntimeFileNotFound("gone"), 404),
        (WorkspaceRuntimeFileRejected("big", status_code=413), 413),
        (SandboxUnavailable("paused"), 503),
    ],
)
def test_read_failures_map_to_something_the_caller_can_act_on(exc, expected) -> None:
    """The two families are parallel, not shared, so both spellings of "not
    there" have to reach the same 404."""
    assert controller._as_http_error(exc, "/workspace/a").status_code == expected


def test_only_real_read_failures_are_dressed_as_a_status() -> None:
    """A defect must surface as a 500, not as "the workspace is busy"."""
    assert not isinstance(TypeError("bug"), controller._READ_FAILURES)
    assert isinstance(SandboxPathNotFound("x"), controller._READ_FAILURES)


@pytest.mark.asyncio
async def test_content_is_served_as_an_attachment() -> None:
    """Workspace files are the person's own content, never markup this origin
    should render."""
    service = _FakeService(running=True, stats=[_stat("/workspace/a.html")])

    response = await controller.read_workspace_file(
        _user(), service, path="a.html", offset=0, length=None
    )

    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.media_type == "application/octet-stream"


class _MissingDirectorySession(_FakeSession):
    async def list_files(self, path):
        raise SandboxPathNotFound(path)


@pytest.mark.asyncio
async def test_a_conversation_that_has_written_nothing_is_empty_not_missing() -> None:
    """A conversation's directory does not exist until the agent writes into it,
    so every new conversation would otherwise open on a 404."""
    service = _FakeService(running=True)
    service.session = _MissingDirectorySession([])

    result = await controller.list_workspace_files(
        _user(), service, path="/workspace/conversations/abc", wake=False
    )

    assert result.entries == []
    assert result.sleeping is False


@pytest.mark.asyncio
async def test_a_missing_file_is_still_a_404() -> None:
    """Only the *listing* forgives absence; asking for one named file does not."""
    service = _FakeService(running=True)
    service.session = _MissingDirectorySession([])

    class _StatMissing(_MissingDirectorySession):
        async def stat_file(self, path):
            raise SandboxPathNotFound(path)

    service.session = _StatMissing([])
    with pytest.raises(HTTPException) as raised:
        await controller.stat_workspace_file(_user(), service, path="nope.md")
    assert raised.value.status_code == 404
