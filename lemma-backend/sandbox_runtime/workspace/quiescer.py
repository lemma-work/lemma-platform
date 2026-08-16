from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
from collections.abc import Callable
import shutil
import signal

from .browser_guard import shed_browser


@dataclass(frozen=True, slots=True)
class QuiesceResult:
    terminated_unmanaged_processes: int


class WorkspaceQuiescer:
    """Remove nonportable compute state before a workspace is suspended."""

    # The browser profile is under the *running user's* home, and which user
    # that is depends on the runtime: the Docker image runs as `appuser`, the
    # E2B template as `user`. Naming only one of them meant the E2B fleet --
    # every production workspace -- carried its Chrome profile through every
    # suspend, because the path being deleted did not exist there. Both are
    # listed rather than derived from $HOME so that a quiesce running as root,
    # which is how the runtime supervisor invokes it, still clears the profile
    # belonging to the user the browser actually ran as.
    _ephemeral_directories = (
        Path("/tmp/lemma-browser"),
        Path("/home/appuser/.agent-browser"),
        Path("/home/user/.agent-browser"),
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
        # Injected so a test never signals a real process. The patterns this
        # matches -- agent-browser, Xvfb -- are things a developer plausibly
        # has running, and a unit test that kills their browser is not a test.
        shed_browser_processes: Callable[[], int] = shed_browser,
    ) -> None:
        self._shed_browser_processes = shed_browser_processes
        self._directories = (
            self._ephemeral_directories
            if ephemeral_directories is None
            else ephemeral_directories
        )
        self._files = (
            self._ephemeral_files if ephemeral_files is None else ephemeral_files
        )
        self._isolated_process_namespace = (
            os.getenv("LEMMA_SANDBOX_PROCESS_NAMESPACE") == "isolated"
            if isolated_process_namespace is None
            else isolated_process_namespace
        )

    async def quiesce(self) -> QuiesceResult:
        terminated = 0
        if self._isolated_process_namespace:
            terminated = await self._terminate_unmanaged_processes()
        else:
            # The blanket sweep above is only safe where the PID namespace
            # holds nothing but us, which is the Docker image -- it is the only
            # runtime that sets the flag. On E2B the namespace also holds
            # envd and E2B's own services, so signalling everything would take
            # the sandbox down with the browser.
            #
            # That left the runtime carrying production with no process
            # cleanup at all: deleting the profile directory does nothing to a
            # Chrome that is still running and still holding 2 GB. Shedding the
            # browser by name is the part that is safe everywhere, and it is
            # the part that mattered.
            terminated = self._shed_browser_processes()
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
