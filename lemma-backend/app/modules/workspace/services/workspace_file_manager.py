"""Workspace file manager."""

import posixpath
from typing import Optional, Union
from uuid import UUID

from sandbox_runtime.errors import SandboxPathNotFound

from sandbox_runtime.paths import RUNTIME_FILESYSTEM_ROOTS, WORKSPACE_ROOT, root_of
from app.modules.workspace.domain.file_types import FileInfo
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WorkspaceFileManager:
    """File manager for workspace operations."""

    def __init__(self, user_id: UUID, cwd: Optional[str] = None):
        self.user_id = user_id
        self.root, self.cwd = self._normalize_cwd(cwd)

    @staticmethod
    def _normalize_cwd(cwd: str | None) -> tuple[str, str]:
        """This session's root, and its directory relative to that root.

        The root is carried rather than assumed, because a workspace created
        before the root moved still has the user's files under the previous
        one. Re-rooting its paths onto the current root does not move the
        files; it points at an empty directory and reports the work missing.

        An absolute cwd is accepted when it is under a root this platform has
        used, which is how a stored conversation cwd arrives. A relative one
        resolves against the current root, which is where new work goes.
        """
        if not cwd:
            return WORKSPACE_ROOT, ""
        if "\x00" in cwd:
            raise ValueError("workspace cwd must not contain a null byte")
        if cwd.startswith("/"):
            base = root_of(posixpath.normpath(cwd))
            if base is None:
                raise ValueError(f"workspace cwd must be relative to {WORKSPACE_ROOT}")
            resolved = posixpath.normpath(cwd)
        else:
            base = WORKSPACE_ROOT
            resolved = posixpath.normpath(posixpath.join(base, cwd))
        if resolved != base and not resolved.startswith(f"{base}/"):
            raise ValueError(f"workspace cwd escapes {base}")
        return base, "" if resolved == base else posixpath.relpath(resolved, base)

    def _workspace_path(self, path: str) -> str:
        """Resolve a caller's path against this session's root.

        An absolute ``/workspace/...`` path is taken as already rooted, rather
        than being joined onto the root a second time. ``lstrip("/")`` alone
        leaves ``workspace/...``, which joined onto a root that already ends in
        it produced
        ``/workspace/conversations/<id>/workspace/conversations/<id>/file`` --
        so every tool documented as accepting "a pod datastore path or a
        workspace path" rejected the absolute form of its own root, while the
        relative form worked. `listen` is where it was noticed; `view_image` and
        every other method here had it too.

        Rooted paths are also *checked* rather than silently re-homed. The guard
        below only ever saw the doubled path, so a path belonging to another
        conversation was quietly rewritten under the caller's own root and read
        from there, instead of being refused.
        """
        base = getattr(self, "root", WORKSPACE_ROOT)
        root = posixpath.normpath(posixpath.join(base, self.cwd) if self.cwd else base)
        # Any root this platform has used counts as already-rooted, not just
        # the current one. A path under the previous root that fell through to
        # the join below was lstripped and re-homed under the caller's own
        # root -- which is the silent re-homing this guard exists to stop,
        # and it came straight back the moment the root moved. Recognised
        # here, such a path is compared against the caller's root and
        # refused, which is what it deserves.
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
