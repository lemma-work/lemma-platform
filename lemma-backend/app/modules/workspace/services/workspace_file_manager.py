"""Workspace file manager."""

import posixpath
from typing import Optional, Union
from uuid import UUID

from agentbox_client import AgentBoxApiError

from app.modules.workspace.domain.file_types import FileInfo
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WorkspaceFileManager:
    """File manager for workspace operations."""

    def __init__(self, user_id: UUID, cwd: Optional[str] = None):
        self.user_id = user_id
        self.cwd = self._normalize_cwd(cwd)

    @staticmethod
    def _normalize_cwd(cwd: str | None) -> str:
        if not cwd:
            return ""
        if "\x00" in cwd or cwd.startswith("/"):
            raise ValueError("workspace cwd must be relative to /workspace")
        root = posixpath.normpath(posixpath.join("/workspace", cwd))
        if root != "/workspace" and not root.startswith("/workspace/"):
            raise ValueError("workspace cwd escapes /workspace")
        return "" if root == "/workspace" else posixpath.relpath(root, "/workspace")

    @staticmethod
    def _is_missing_error(error: AgentBoxApiError) -> bool:
        return error.status_code == 404 and error.code == "FILE_NOT_FOUND"

    def _workspace_path(self, path: str) -> str:
        root = posixpath.normpath(
            posixpath.join("/workspace", self.cwd) if self.cwd else "/workspace"
        )
        candidate = posixpath.normpath(posixpath.join(root, path.lstrip("/")))
        if candidate != root and not candidate.startswith(f"{root}/"):
            raise ValueError("workspace file path escapes its configured root")
        return candidate

    def _relative_workspace_path(self, path: str) -> str:
        root = self._workspace_path("")
        return posixpath.relpath(path, root)

    async def _get_workspace_session(self):
        from app.modules.workspace.services.workspace_sandbox_service import (
            WorkspaceSandboxService,
        )

        service = WorkspaceSandboxService()
        return await service.get_session(
            user_id=self.user_id,
            pod_id=None,
            session_id=f"files-{self.user_id.hex}",
            initial_cwd="/workspace",
            close_on_exit=False,
        )

    async def list_files(self, path: str) -> list[FileInfo]:
        """List files in a directory."""
        session = await self._get_workspace_session()
        runtime_path = self._workspace_path(path)
        async with session:
            try:
                entries = await session.list_files(runtime_path, timeout=30)
            except AgentBoxApiError as exc:
                if self._is_missing_error(exc):
                    return []
                raise
        if not entries:
            return []

        return [
            FileInfo(
                name=posixpath.basename(item.path),
                path=self._relative_workspace_path(item.path),
                type=item.kind.value,
                size=item.size_bytes,
                last_modified=item.modified_at.isoformat(),
            )
            for item in entries
        ]

    async def get_file_info(self, path: str) -> Optional[FileInfo]:
        """Get file information."""
        session = await self._get_workspace_session()
        try:
            async with session:
                item = await session.stat_file(
                    self._workspace_path(path),
                    timeout=30,
                )
        except AgentBoxApiError as exc:
            if self._is_missing_error(exc):
                return None
            raise
        return FileInfo(
            name=posixpath.basename(item.path),
            path=self._relative_workspace_path(item.path),
            type=item.kind.value,
            size=item.size_bytes,
            last_modified=item.modified_at.isoformat(),
        )

    async def read_file(self, path: str) -> Union[bytes, str]:
        """Read a file."""
        session = await self._get_workspace_session()
        try:
            async with session:
                bytes_data = await session.read_file(
                    self._workspace_path(path),
                    timeout=60,
                )
        except AgentBoxApiError as exc:
            if self._is_missing_error(exc):
                raise FileNotFoundError(f"File {path} not found") from exc
            raise
        try:
            return bytes_data.decode("utf-8")
        except UnicodeDecodeError:
            return bytes_data

    async def write_file(self, path: str, content: Union[bytes, str]) -> FileInfo:
        """Write a file."""
        if isinstance(content, str):
            content = content.encode("utf-8")

        session = await self._get_workspace_session()
        runtime_path = self._workspace_path(path)
        async with session:
            item = await session.write_file(runtime_path, content, timeout=60)
        return FileInfo(
            name=posixpath.basename(item.path),
            path=self._relative_workspace_path(item.path),
            type=item.kind.value,
            size=item.size_bytes,
            last_modified=item.modified_at.isoformat(),
        )

    async def delete_file(self, path: str) -> None:
        """Delete a file or directory idempotently."""
        session = await self._get_workspace_session()
        try:
            async with session:
                await session.delete_file(
                    self._workspace_path(path),
                    recursive=True,
                    timeout=30,
                )
        except AgentBoxApiError as exc:
            if self._is_missing_error(exc):
                return
            raise
