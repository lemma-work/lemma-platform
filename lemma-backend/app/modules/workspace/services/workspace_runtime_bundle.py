"""Keeping a sandbox's first-party Python current, without replacing the sandbox.

A mixin rather than a collaborator, for the reason ``SandboxVolumeMixin`` is one:
it needs the service's manager client and has no state of its own beyond a cache,
and ``workspace_sandbox_service`` is at the size the architecture ratchet allows.

The shape of the work is the same as ``_ensure_workspace_directory`` beside it --
something that must be true inside the sandbox before a session uses it, made
cheap by remembering that it already is. The difference is what "already" means:
a directory is a property of the disk, while the overlay is a property of the
*sandbox*, so the key is the allocation and the generation, and a sandbox that
was replaced re-probes rather than trusting a remembered answer.

**Failure degrades, it does not propagate.** A sandbox that cannot take the
overlay still has the image's own copy of the first-party code, which is what
every sandbox runs today. Failing a tool call because a 1.6 MB upload timed out
would be a worse outcome than the staleness it was trying to fix -- and staleness
is the status quo, not a regression this introduced.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.core.log.log import get_logger
from app.modules.workspace.infrastructure.runtime_bundle import (
    RuntimeBundle,
    runtime_bundle,
)
from app.modules.workspace.process_output import TERMINAL_PROCESS_STATES
from app.modules.workspace.providers.base import (
    ProviderFailed,
    ProviderGone,
    ProviderNotReady,
    ProviderRejected,
)
from sandbox_runtime import runtime_install
from sandbox_runtime.errors import SandboxError
from sandbox_runtime.protocol import ProcessState, WorkloadKind

logger = get_logger(__name__)

#: Where the overlay lives. Outside `/workspace` on purpose: this is platform
#: code, not the user's, and putting it in their project root would put it in
#: their file tree, in their exports, and within reach of an agent's `rm`.
RUNTIME_ROOT = "/opt/lemma-runtime"

#: Staging paths. `/tmp` because they are consumed once and must not survive --
#: the installer deletes them itself, and a pause would otherwise carry a
#: superseded archive forward for the life of the sandbox.
INSTALLER_PATH = "/tmp/lemma-runtime-install.py"
ARCHIVE_PATH = "/tmp/lemma-runtime-bundle.zip"

#: The interpreter that owns the site-packages the `.pth` must land in. Naming
#: it explicitly rather than `python3`: the overlay is only ever ahead of *this*
#: environment, and a different interpreter would write the file somewhere it is
#: never read.
_SANDBOX_PYTHON = "/opt/lemma-python/bin/python"

#: Long enough to upload 1.6 MB and unpack it on one vCPU, short enough that a
#: wedged sandbox does not hold a session open. Only paid when the version moves.
_INSTALL_BUDGET_SECONDS = 180.0

#: One round trip. A probe that has to retry is a sandbox with worse problems.
_PROBE_BUDGET_SECONDS = 20.0

#: Everything the provider layer raises for "this sandbox could not do that".
#: Named rather than caught broadly so a mistake in *our* code still propagates.
_SANDBOX_FAILURES = (
    ProviderFailed,
    ProviderGone,
    ProviderNotReady,
    ProviderRejected,
    SandboxError,
    OSError,
    asyncio.TimeoutError,
)


def _deadline(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def install_command(*, version: str, requires: Sequence[str]) -> str:
    """The shell command that installs a delivered bundle.

    Module-level so the real-sandbox test runs the command this actually emits
    rather than a copy of it that can drift.

    The overlay is root-owned, and so is the site-packages the `.pth` goes in --
    verified against a live sandbox, where the workspace user cannot even
    `mkdir /opt/lemma-runtime`. E2B's user has passwordless sudo, so elevation
    is available; a fabric without it falls through to an unelevated run, which
    fails cleanly and leaves the baked copy in place. That is better than
    branching on the provider: Docker's image already carries current
    first-party code, because rebuilding it there costs a container rather than
    somebody's disk, so an overlay has nothing to fix.
    """
    return (
        "sudo -n true 2>/dev/null && SUDO='sudo -n' || SUDO=''; "
        f"$SUDO {_SANDBOX_PYTHON} {INSTALLER_PATH} install "
        f"--root {RUNTIME_ROOT} "
        f"--archive {ARCHIVE_PATH} "
        f"--version {version} "
        f"--requires {','.join(requires)}"
    )


class WorkspaceRuntimeBundleMixin:
    """``_ensure_runtime_bundle``, mixed into the workspace sandbox service."""

    #: (loop, user, allocation, generation) -> the version known to be installed.
    #: Class-level, like the directory caches beside it: the answer is about a
    #: sandbox rather than about whoever happens to hold a service instance.
    _installed_bundles: dict[tuple[int, UUID, str, int], str] = {}
    _inflight_bundles: dict[tuple[int, UUID, str, int], asyncio.Task[bool]] = {}

    def _runtime_bundle(self) -> RuntimeBundle | None:
        """The bundle this service installs.

        A method rather than a module-level call at the point of use, so a test
        supplies one by overriding a seam instead of patching the name inside
        the module under test -- a double planted in the subject certifies the
        half nobody wrote and survives a rename that should have failed it.
        """
        return runtime_bundle()

    def _bundle_cache_key(
        self, user_id: UUID, sandbox_info
    ) -> tuple[int, UUID, str, int] | None:
        """Identity for "the overlay is installed", which belongs to the sandbox.

        Unlike a directory, the overlay does not live on a disk that outlives its
        container: it is written into the sandbox's own filesystem. So a new
        allocation has no overlay however healthy the disk is, and a recreated
        disk implies a new allocation anyway. Both are in the key, which makes
        the answer conservative in the only direction that is safe -- a
        re-probe costs one command, a wrong "already installed" costs a sandbox
        running code we believe we replaced.
        """
        if (
            sandbox_info.allocation_id is None
            or sandbox_info.storage_generation is None
        ):
            return None
        return (
            id(asyncio.get_running_loop()),
            user_id,
            sandbox_info.allocation_id,
            sandbox_info.storage_generation,
        )

    async def _ensure_runtime_bundle(self, user_id: UUID, sandbox_info) -> None:
        """Install the configured bundle into this sandbox, at most once.

        The warm path is a dictionary lookup and no I/O at all, which is the
        whole reason the installed version is remembered rather than asked for:
        this runs on the way to every tool call, on a path whose remaining cost
        is already one provider round trip.
        """
        bundle = self._runtime_bundle()
        if bundle is None:
            return
        key = self._bundle_cache_key(user_id, sandbox_info)
        if key is not None and self._installed_bundles.get(key) == bundle.version:
            return

        task = self._inflight_bundles.get(key) if key is not None else None
        if task is None:
            task = asyncio.create_task(self._install_bundle(user_id, bundle))
            if key is not None:
                self._inflight_bundles[key] = task
                task.add_done_callback(
                    lambda done: (
                        self._inflight_bundles.pop(key, None)
                        if self._inflight_bundles.get(key) is done
                        else None
                    )
                )
        installed = await asyncio.shield(task)
        # Only a success is remembered. Recording the attempt instead would turn
        # one failed upload into a sandbox pinned to the image's older copy for
        # the life of this process, with the warm path skipping every retry.
        if installed and key is not None:
            self._installed_bundles[key] = bundle.version

    async def _install_bundle(self, user_id: UUID, bundle: RuntimeBundle) -> bool:
        """Install it, reporting whether the sandbox now has this version."""
        client = self._get_manager_client()
        try:
            if await self._installed_version(client, user_id) == bundle.version:
                return True
            await self._deliver(client, user_id, bundle)
            await self._run_installer(client, user_id, bundle)
        except _SANDBOX_FAILURES as exc:
            # Deliberately swallowed, and deliberately logged in full. The
            # sandbox keeps the image's copy of the first-party code, so the
            # cost of this is staleness -- the same staleness every sandbox has
            # today -- rather than a failed tool call.
            logger.warning(
                "workspace.runtime_bundle.install_failed.degraded",
                user_id=str(user_id),
                version=bundle.version,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return False
        logger.info(
            "workspace.runtime_bundle.installed",
            user_id=str(user_id),
            version=bundle.version,
        )
        return True

    async def _installed_version(self, client, user_id: UUID) -> str | None:
        """What the sandbox itself says is installed, or None.

        Read rather than inferred: the stamp is written by the installer only
        after its smoke test passed, so its presence is the sandbox's own claim
        that the overlay works -- which is a stronger statement than anything
        this process could remember.
        """
        try:
            stamp = await client.read_file(
                user_id,
                f"{RUNTIME_ROOT}/{runtime_install.CURRENT_LINK}/"
                f"{runtime_install.STAMP_NAME}",
                deadline_at=_deadline(_PROBE_BUDGET_SECONDS),
            )
        except _SANDBOX_FAILURES:
            # Absent is the normal answer for a sandbox that has never had one.
            return None
        return stamp.decode("utf-8", "replace").strip() or None

    async def _deliver(self, client, user_id: UUID, bundle: RuntimeBundle) -> None:
        """Put the installer and the archive where the sandbox can reach them.

        Through the files API rather than a shell: a single argv entry caps at
        128 KB, so the archive could not travel on a command line even if
        putting 1.6 MB of base64 there were a reasonable thing to do.
        """
        deadline_at = _deadline(_INSTALL_BUDGET_SECONDS)
        await client.write_file(
            user_id,
            INSTALLER_PATH,
            Path(runtime_install.__file__).read_bytes(),
            deadline_at=deadline_at,
        )
        await client.write_file(
            user_id,
            ARCHIVE_PATH,
            bundle.archive,
            deadline_at=deadline_at,
            # The provider verifies this before the bytes are accepted, so a
            # truncated upload is refused rather than unpacked.
            expected_sha256=bundle.archive_sha256.removeprefix("sha256:"),
        )

    async def _run_installer(
        self, client, user_id: UUID, bundle: RuntimeBundle
    ) -> None:
        deadline_at = _deadline(_INSTALL_BUDGET_SECONDS)
        operation_id = uuid4()
        command = install_command(version=bundle.version, requires=bundle.requires)
        await client.start_process(
            WorkloadKind.WORKSPACE,
            user_id,
            operation_id=operation_id,
            deadline_at=deadline_at,
            cwd="/tmp",
            shell_command=command,
        )
        snapshot = await self._await_exit(client, user_id, operation_id, deadline_at)
        if snapshot is None or snapshot.exit_code != 0:
            raise ProviderFailed(
                f"the runtime bundle installer exited "
                f"{getattr(snapshot, 'exit_code', 'without reporting')}: "
                f"{self._tail(snapshot)}"
            )

    async def _await_exit(self, client, user_id: UUID, operation_id: UUID, deadline_at):
        """Wait on the process's own completion, never on the clock."""
        after = 0
        while datetime.now(timezone.utc) < deadline_at:
            snapshot = await client.read_process_output(
                WorkloadKind.WORKSPACE,
                user_id,
                operation_id,
                deadline_at=deadline_at,
                after_sequence=after,
                wait_seconds=5,
            )
            after = snapshot.next_sequence
            if snapshot.state in TERMINAL_PROCESS_STATES:
                return snapshot
        return None

    @staticmethod
    def _tail(snapshot) -> str:
        """Enough of the installer's output to say what went wrong."""
        if snapshot is None or snapshot.state is ProcessState.RUNNING:
            return "no output"
        text = "".join(
            chunk.data.decode("utf-8", "replace") for chunk in snapshot.chunks
        )
        return text.strip()[-500:] or "no output"


__all__ = [
    "ARCHIVE_PATH",
    "INSTALLER_PATH",
    "RUNTIME_ROOT",
    "WorkspaceRuntimeBundleMixin",
    "install_command",
]
