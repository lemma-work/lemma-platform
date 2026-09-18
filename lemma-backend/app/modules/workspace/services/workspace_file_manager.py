"""Workspace file manager."""

import posixpath
from typing import Optional, Union
from uuid import UUID

from sandbox_runtime.errors import SandboxPathNotFound

from sandbox_runtime.paths import RUNTIME_FILESYSTEM_ROOTS, WORKSPACE_ROOT
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
        """This session's directory, relative to the workspace root.

        An absolute cwd is accepted when it is under that root, which is how a
        stored conversation cwd arrives; a relative one resolves against it.
        """
        if not cwd:
            return ""
        if "\x00" in cwd:
            raise ValueError("workspace cwd must not contain a null byte")
        if cwd.startswith("/"):
            resolved = posixpath.normpath(cwd)
        else:
            resolved = posixpath.normpath(posixpath.join(WORKSPACE_ROOT, cwd))
        if resolved != WORKSPACE_ROOT and not resolved.startswith(f"{WORKSPACE_ROOT}/"):
            raise ValueError(f"workspace cwd must be relative to {WORKSPACE_ROOT}")
        return (
            ""
            if resolved == WORKSPACE_ROOT
            else posixpath.relpath(resolved, WORKSPACE_ROOT)
        )

    def _workspace_path(self, path: str) -> str:
        """Resolve a caller's path against this session's root.

        An absolute in-root path is taken as already rooted, rather
        than being joined onto the root a second time. ``lstrip("/")`` alone
        leaves the root's own leading segment, which joined onto a root that
        already ends in it produced a doubled path --
        so every tool documented as accepting "a pod datastore path or a
        workspace path" rejected the absolute form of its own root, while the
        relative form worked. `listen` is where it was noticed; `view_image` and
        every other method here had it too.

        Rooted paths are also *checked* rather than silently re-homed. The guard
        below only ever saw the doubled path, so a path belonging to another
        conversation was quietly rewritten under the caller's own root and read
        from there, instead of being refused.
        """
        root = posixpath.normpath(
            posixpath.join(WORKSPACE_ROOT, self.cwd) if self.cwd else WORKSPACE_ROOT
        )
        # An absolute path under any root the runtime serves counts as already
        # rooted. Falling through to the join below would lstrip it and re-home
        # it under the caller's own directory -- reading another conversation's
        # file as if it were yours -- which is the silent re-homing this guard
        # exists to stop.
        if any(
            path == known or path.startswith(f"{known}/")
            for known in RUNTIME_FILESYSTEM_ROOTS
        ):
            candidate = posixpath.normpath(path)
        else:
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
            initial_cwd=WORKSPACE_ROOT,
            close_on_exit=False,
        )

    async def list_files(self, path: str) -> list[FileInfo]:
        """List files in a directory."""
        session = await self._get_workspace_session()
        runtime_path = self._workspace_path(path)
        async with session:
            try:
                entries = await session.list_files(runtime_path, timeout=30)
            except SandboxPathNotFound:
                return []
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
        except SandboxPathNotFound:
            return None
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
        except SandboxPathNotFound as exc:
            raise FileNotFoundError(f"File {path} not found") from exc
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
        except SandboxPathNotFound:
            return
