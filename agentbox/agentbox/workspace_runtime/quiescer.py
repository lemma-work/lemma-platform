from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal


@dataclass(frozen=True, slots=True)
class QuiesceResult:
    terminated_unmanaged_processes: int


class WorkspaceQuiescer:
    """Remove nonportable compute state before a workspace is suspended."""

    _ephemeral_directories = (
        Path("/tmp/agentbox-browser"),
        Path("/home/appuser/.agent-browser"),
        Path("/workspace/.browser-profile"),
    )
    _ephemeral_files = (
        Path("/tmp/.X99-lock"),
        Path("/workspace/agent-browser.json"),
    )

    def __init__(
        self,
        *,
        ephemeral_directories: tuple[Path, ...] | None = None,
        ephemeral_files: tuple[Path, ...] | None = None,
        isolated_process_namespace: bool | None = None,
    ) -> None:
        self._directories = (
            self._ephemeral_directories
            if ephemeral_directories is None
            else ephemeral_directories
        )
        self._files = (
            self._ephemeral_files if ephemeral_files is None else ephemeral_files
        )
        self._isolated_process_namespace = (
            os.getenv("AGENTBOX_SANDBOX_PROCESS_NAMESPACE") == "isolated"
            if isolated_process_namespace is None
            else isolated_process_namespace
        )

    async def quiesce(self) -> QuiesceResult:
        terminated = 0
        if self._isolated_process_namespace:
            terminated = await self._terminate_unmanaged_processes()
        for path in self._directories:
            shutil.rmtree(path, ignore_errors=True)
        for path in self._files:
            path.unlink(missing_ok=True)
        return QuiesceResult(terminated_unmanaged_processes=terminated)

    @staticmethod
    async def _terminate_unmanaged_processes() -> int:
        protected = {1, os.getpid(), os.getppid()}
        process_ids = tuple(
            int(path.name)
            for path in Path("/proc").iterdir()
            if path.name.isdigit() and int(path.name) not in protected
        )
        for process_id in process_ids:
            try:
                os.kill(process_id, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if process_ids:
            await asyncio.sleep(0.1)
        for process_id in process_ids:
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                continue
            try:
                os.kill(process_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return len(process_ids)
