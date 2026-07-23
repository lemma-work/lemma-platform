"""Workspace file manager."""

from datetime import datetime
from pathlib import Path
import posixpath
import shutil
import tempfile
from typing import Optional, Union
from uuid import UUID

from agentbox_client import AgentBoxApiError

from app.core.config import settings
from app.modules.workspace.domain.file_types import FileInfo
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WorkspaceFileManager:
    """File manager for workspace operations."""

    def __init__(self, user_id: UUID, cwd: Optional[str] = None):
        self.user_id = user_id
        self.cwd = self._normalize_cwd(cwd)
        self._local_base: Path | None = None

        if settings.environment == "testing":
            root = (Path(tempfile.gettempdir()) / "lemma_test_storage").resolve()
            self._local_base = root / str(self.user_id)
            if self.cwd:
                self._local_base = self._local_base / self.cwd
            self._local_base = self._local_base.resolve()
            self._local_base.mkdir(parents=True, exist_ok=True)

    def _local_path(self, path: str) -> Path:
        if not self._local_base:
            raise RuntimeError("Local storage is not configured")
        if "\x00" in path or Path(path).is_absolute():
            raise ValueError("local file path must be relative")
        candidate = (self._local_base / path).resolve()
        try:
            candidate.relative_to(self._local_base)
        except ValueError as exc:
            raise ValueError("local file path escapes its configured root") from exc
        return candidate

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
        if self._local_base:
            root = self._local_path(path)
            if not root.exists():
                return []

            results = []
            for file_path in root.rglob("*"):
                if not file_path.is_file():
                    continue
                relative_path = str(file_path.relative_to(self._local_base))
                stat = file_path.stat()
                results.append(
                    FileInfo(
                        name=file_path.name,
                        path=relative_path,
                        type="file",
                        size=stat.st_size,
                        last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    )
                )
            return results

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
        if self._local_base:
            file_path = self._local_path(path)
            if not file_path.exists() or not file_path.is_file():
                return None
            stat = file_path.stat()
            return FileInfo(
                name=file_path.name,
                path=path,
                type="file",
                size=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            )

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
        if self._local_base:
            file_path = self._local_path(path)
            if not file_path.exists():
                raise FileNotFoundError(f"File {path} not found")
            bytes_data = file_path.read_bytes()
            try:
                return bytes_data.decode("utf-8")
            except UnicodeDecodeError:
                return bytes_data

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

        if self._local_base:
            file_path = self._local_path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)
            return FileInfo(
                name=file_path.name,
                path=path,
                type="file",
                size=len(content),
                last_modified=datetime.now().isoformat(),
            )

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
        if self._local_base:
            file_path = self._local_path(path)
            if file_path.is_symlink() or file_path.is_file():
                file_path.unlink()
            elif file_path.is_dir():
                shutil.rmtree(file_path)
            return

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
