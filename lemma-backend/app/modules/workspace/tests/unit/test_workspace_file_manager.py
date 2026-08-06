from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from agentbox_client import AgentBoxApiError
from agentbox_client.models import (
    AgentBoxErrorBody,
    AgentBoxErrorResponse,
    RetryDisposition,
)
import httpx
import pytest

from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


class _FakeWorkspaceSession:
    def __init__(self, failures: dict[str, AgentBoxApiError] | None = None):
        self.operations: list[tuple] = []
        self.content = b"test"
        self.failures = failures or {}

    def _raise_if_failed(self, operation: str) -> None:
        error = self.failures.get(operation)
        if error is not None:
            raise error

    def _stat(self, path: str):
        return SimpleNamespace(
            path=path,
            kind=SimpleNamespace(value="file"),
            size_bytes=len(self.content),
            modified_at=datetime.now(timezone.utc),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        del exc_type, exc_val, exc_tb

    async def list_files(self, path: str, *, timeout: int):
        self._raise_if_failed("list")
        self.operations.append(("list", path, timeout))
        return (self._stat(f"{path}/note.txt"),)

    async def stat_file(self, path: str, *, timeout: int):
        self._raise_if_failed("stat")
        self.operations.append(("stat", path, timeout))
        return self._stat(path)

    async def write_file(self, path: str, data: bytes, *, timeout: int):
        self.operations.append(("write", path, timeout))
        self.content = data
        return self._stat(path)

    async def read_file(self, path: str, *, timeout: int):
        self._raise_if_failed("read")
        self.operations.append(("read", path, timeout))
        return self.content

    async def delete_file(self, path: str, *, recursive: bool, timeout: int):
        self._raise_if_failed("delete")
        self.operations.append(("delete", path, recursive, timeout))


class _FakeWorkspaceService:
    def __init__(self, session: _FakeWorkspaceSession):
        self.session = session

    async def get_session(self, **kwargs):
        del kwargs
        return self.session


def _api_error(*, status_code: int, code: str) -> AgentBoxApiError:
    response = httpx.Response(status_code, request=httpx.Request("GET", "http://test"))
    error = AgentBoxErrorResponse(
        error=AgentBoxErrorBody(
            code=code,
            message=code,
            retry=(
                RetryDisposition.WAIT
                if status_code >= 500
                else RetryDisposition.DO_NOT_RETRY
            ),
        )
    )
    return AgentBoxApiError(response, error)


def _configure_remote_manager(
    monkeypatch: pytest.MonkeyPatch, session: _FakeWorkspaceSession
) -> WorkspaceFileManager:
    monkeypatch.setattr(
        "app.modules.workspace.services.workspace_sandbox_service.WorkspaceSandboxService",
        lambda: _FakeWorkspaceService(session),
    )
    return WorkspaceFileManager(uuid4(), cwd="conversations/abc")


@pytest.mark.asyncio
async def test_workspace_file_manager_uses_sandbox_session(monkeypatch):
    session = _FakeWorkspaceSession()
    manager = _configure_remote_manager(monkeypatch, session)

    listed = await manager.list_files("")
    written = await manager.write_file("note.txt", "test")
    read_back = await manager.read_file("note.txt")
    await manager.delete_file("note.txt")

    assert session.operations == [
        ("list", "/workspace/conversations/abc", 30),
        ("write", "/workspace/conversations/abc/note.txt", 60),
        ("read", "/workspace/conversations/abc/note.txt", 60),
        ("delete", "/workspace/conversations/abc/note.txt", True, 30),
    ]
    assert listed[0].path == "note.txt"
    assert written.path == "note.txt"
    assert read_back == "test"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "stat", "read", "delete"])
async def test_workspace_file_manager_does_not_hide_provider_outage(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    outage = _api_error(status_code=503, code="PROVIDER_UNAVAILABLE")
    manager = _configure_remote_manager(
        monkeypatch, _FakeWorkspaceSession({operation: outage})
    )
    calls = {
        "list": lambda: manager.list_files("note.txt"),
        "stat": lambda: manager.get_file_info("note.txt"),
        "read": lambda: manager.read_file("note.txt"),
        "delete": lambda: manager.delete_file("note.txt"),
    }

    with pytest.raises(AgentBoxApiError) as raised:
        await calls[operation]()

    assert raised.value is outage


@pytest.mark.asyncio
async def test_workspace_file_manager_converts_only_typed_missing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _api_error(status_code=404, code="FILE_NOT_FOUND")
    manager = _configure_remote_manager(
        monkeypatch, _FakeWorkspaceSession({"read": missing, "stat": missing})
    )

    assert await manager.get_file_info("missing.txt") is None
    with pytest.raises(FileNotFoundError):
        await manager.read_file("missing.txt")


def test_workspace_file_manager_rejects_escaping_cwd_and_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    with pytest.raises(ValueError, match="cwd escapes"):
        WorkspaceFileManager(uuid4(), cwd="../../outside")

    manager = WorkspaceFileManager(uuid4(), cwd="conversations/abc")
    with pytest.raises(ValueError, match="escapes"):
        manager._workspace_path("../../outside")
