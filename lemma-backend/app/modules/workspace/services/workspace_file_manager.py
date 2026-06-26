"""Workspace file manager."""

import base64
from datetime import datetime
import json
from typing import Optional, Union
from uuid import UUID
from pathlib import Path
import shlex
import tempfile

from app.core.config import settings
from app.modules.workspace.domain.file_types import FileInfo
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WorkspaceFileManager:
    """File manager for workspace operations."""

    def __init__(self, user_id: UUID, cwd: Optional[str] = None):
        self.user_id = user_id
        self.cwd = cwd.strip("/") if cwd else ""
        self._local_base: Path | None = None

        if settings.environment == "testing":
            root = Path(tempfile.gettempdir()) / "lemma_test_storage"
            self._local_base = root / str(self.user_id)
            if self.cwd:
                self._local_base = self._local_base / self.cwd
            self._local_base.mkdir(parents=True, exist_ok=True)

    def _local_path(self, path: str) -> Path:
        if not self._local_base:
            raise RuntimeError("Local storage is not configured")
        return self._local_base / path

    def _workspace_path(self, path: str) -> str:
        relative = path.lstrip("/")
        if self.cwd and relative:
            return f"{self.cwd}/{relative}"
        if self.cwd:
            return self.cwd
        return relative

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
            script = (
                "import json, pathlib; "
                f"p=pathlib.Path({runtime_path or '.'!r}); "
                "items=[]; "
                "items=[{'name': x.name, 'path': str(x), "
                "'type': 'directory' if x.is_dir() else 'file', "
                "'size': x.stat().st_size} for x in p.iterdir()] if p.exists() else []; "
                "print(json.dumps(items))"
            )
            result = await session.exec_command(
                cmd=f"python -c {shlex.quote(script)}",
                timeout=30,
            )
        if not result.get("success"):
            return []

        base_prefix = f"{self.cwd}/" if self.cwd else ""
        results = []
        for item in json.loads(result.get("stdout") or "[]"):
            item_path = item["path"]
            if item_path.startswith("/workspace/"):
                item_path = item_path[len("/workspace/") :]
            results.append(
                FileInfo(
                    name=item["name"],
                    path=item_path[len(base_prefix) :]
                    if base_prefix and item_path.startswith(base_prefix)
                    else item_path,
                    type=item.get("type", "file"),
                    size=item.get("size"),
                    last_modified=item.get("last_modified"),
                )
            )
        return results

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

        parent = str(Path(path).parent)
        if parent == ".":
            parent = ""
        for item in await self.list_files(parent):
            if item.path == path:
                return item
        return None

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
        async with session:
            result = await session.exec_command(
                cmd=f"base64 -w 0 {shlex.quote(self._workspace_path(path))}",
                timeout=60,
            )
        if not result.get("success"):
            raise FileNotFoundError(f"File {path} not found")
        bytes_data = base64.b64decode(result.get("stdout") or "")
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
        encoded = base64.b64encode(content).decode("ascii")
        command = (
            f"mkdir -p {shlex.quote(str(Path(runtime_path).parent))} && "
            f"printf %s {shlex.quote(encoded)} | base64 -d > {shlex.quote(runtime_path)}"
        )
        async with session:
            result = await session.exec_command(cmd=command, timeout=60)
        if not result.get("success"):
            raise RuntimeError(result.get("stderr") or result.get("error") or "Write failed")
        base_prefix = f"{self.cwd}/" if self.cwd else ""
        result_path = runtime_path
        if base_prefix and result_path.startswith(base_prefix):
            result_path = result_path[len(base_prefix) :]
        return FileInfo(
            name=Path(path).name,
            path=result_path,
            type="file",
            size=len(content),
            last_modified=datetime.now().isoformat(),
        )

    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        if self._local_base:
            file_path = self._local_path(path)
            if file_path.exists():
                file_path.unlink()
            return

        session = await self._get_workspace_session()
        async with session:
            await session.exec_command(
                cmd=f"rm -rf {shlex.quote(self._workspace_path(path))}",
                timeout=30,
            )
