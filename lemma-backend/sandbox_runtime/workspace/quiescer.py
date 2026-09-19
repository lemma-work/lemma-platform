from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
import os
from pathlib import Path
from collections.abc import Awaitable, Callable
import shutil
import signal

from .browser_guard import shed_browser
from sandbox_runtime.paths import HOME_ROOT


@dataclass(frozen=True, slots=True)
class QuiesceResult:
    terminated_unmanaged_processes: int


class WorkspaceQuiescer:
    """Remove nonportable compute state before a workspace is suspended."""

    # Three of the paths this used to name no longer exist. `/home/appuser` and
    # `/home/user` were once different runtimes' homes, and naming only one of
    # them carried the E2B fleet's Chrome profile through every suspend; #744
    # gave the Docker image's `appuser` `--home-dir /home/user`, so the two have
    # converged and only the survivor is listed. `/workspace/.browser-profile`
    # and `/workspace/agent-browser.json` went the same way when the durable
    # root moved into the home. Deleting a path that cannot exist is not free:
    # it reads as coverage this had stopped providing.
    _ephemeral_directories = (
        Path("/tmp/lemma-browser"),
        Path(f"{HOME_ROOT}/.agent-browser"),
    )
    _ephemeral_files = (Path("/tmp/.X99-lock"),)

    # Inside the profile that *does* survive, the few files that cannot.
    #
    # This class removes nonportable compute state, and a suspend ends every
    # process -- so anything naming one is a lie by the time the sandbox comes
    # back. Chrome refuses to start against a `SingletonLock` it believes
    # another instance holds, and `DevToolsActivePort` advertises a port nobody
    # is listening on.
    #
    # Everything else in the profile is emphatically portable: `Cookies` is
    # SQLite, `Local Storage` is LevelDB, and both are built to survive a
    # process that stopped without warning -- which is also why the memory
    # guard's SIGKILL does not cost a login. Deleting the whole directory was
    # over-broad for this class's own purpose, and it is the reason a sign-in
    # used to last exactly as long as the sandbox did.

    def __init__(
        self,
        *,
        ephemeral_directories: tuple[Path, ...] | None = None,
        ephemeral_files: tuple[Path, ...] | None = None,
        isolated_process_namespace: bool | None = None,
        # Injected so a test never signals a real process. The patterns this
        # matches -- agent-browser, Xvfb -- are things a developer plausibly
        # has running, and a unit test that kills their browser is not a test.
        shed_browser_processes: Callable[[], Awaitable[object] | object] = shed_browser,
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
        # The browser closes first, on every fabric, before anything is
        # signalled or swept.
        #
        # This used to be in the `else` -- so Docker, which sets
        # `LEMMA_SANDBOX_PROCESS_NAMESPACE=isolated` and takes the branch
        # above, never closed the browser at all. It went straight to a
        # blanket SIGTERM/SIGKILL of every process. Measured on this image,
        # one second after a login: a graceful close keeps the session and a
        # signal does not, whether SIGTERM to all eleven Chrome processes or
        # to the browser process alone. So the fabric most people develop on
        # was losing exactly the logins this branch exists to preserve, and
        # the suspend/resume story was only ever true on E2B.
        #
        # Ordering, not duplication: the sweep below still runs and still
        # ends anything the close left behind.
        closed = self._shed_browser_processes()
        if inspect.isawaitable(closed):
            closed = await closed
        closed = bool(closed)

        terminated = 0
        if self._isolated_process_namespace:
            terminated = await self._terminate_unmanaged_processes()
        else:
            # The blanket sweep is only safe where the PID namespace holds
            # nothing but us, which is the Docker image -- it is the only
            # runtime that sets the flag. On E2B the namespace also holds
            # envd and E2B's own services, so signalling everything would
            # take the sandbox down with the browser. There, the close above
            # is the whole of the cleanup, which is why it reports itself.
            terminated = int(closed)
        for path in self._directories:
            shutil.rmtree(path, ignore_errors=True)
        for path in self._files:
            path.unlink(missing_ok=True)
        # No lock-file cleanup. This removed `SingletonLock`,
        # `SingletonSocket`, `SingletonCookie` and `DevToolsActivePort` on the
        # theory that a file naming a dead process would stop the next Chrome
        # starting. Measured on a real sandbox instead: `kill -9` the browser,
        # leave all four behind, and `agent-browser open` starts a browser and
        # rewrites them. Chrome checks whether the pid a lock names is alive.
        #
        # Deleting them was not free either. `DevToolsActivePort` is the only
        # record of a running browser's port, so removing it while one was up
        # left a browser nothing could find -- which is exactly what happened
        # when `start-browser` did the same thing on every call.
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
