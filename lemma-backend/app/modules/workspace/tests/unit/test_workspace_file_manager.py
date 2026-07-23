from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


class _FakeWorkspaceSession:
    def __init__(self):
        self.operations: list[tuple] = []
        self.content = b"test"

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
        self.operations.append(("list", path, timeout))
        return (self._stat(f"{path}/note.txt"),)

    async def stat_file(self, path: str, *, timeout: int):
        self.operations.append(("stat", path, timeout))
        return self._stat(path)

    async def write_file(self, path: str, data: bytes, *, timeout: int):
        self.operations.append(("write", path, timeout))
        self.content = data
        return self._stat(path)

    async def read_file(self, path: str, *, timeout: int):
        self.operations.append(("read", path, timeout))
        return self.content

    async def delete_file(self, path: str, *, recursive: bool, timeout: int):
        self.operations.append(("delete", path, recursive, timeout))


class _FakeWorkspaceService:
    def __init__(self, session: _FakeWorkspaceSession):
        self.session = session

    async def get_session(self, **kwargs):
        del kwargs
        return self.session


@pytest.mark.asyncio
async def test_workspace_file_manager_uses_sandbox_session(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    session = _FakeWorkspaceSession()
    monkeypatch.setattr(
        "app.modules.workspace.services.workspace_sandbox_service.WorkspaceSandboxService",
        lambda: _FakeWorkspaceService(session),
    )

    manager = WorkspaceFileManager(uuid4(), cwd="conversations/abc")

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
