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
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.workspace.infrastructure.runtime_bundle import (
    RuntimeBundle,
    runtime_bundle,
)
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.services.browser_proxy import (
    BROWSER_PROXY_DECISION_PATH,
    browser_proxy_for,
    decision_bytes,
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
from sandbox_runtime.protocol import (
    ProcessOutputSnapshot,
    ProcessState,
    WorkloadKind,
)

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

#: How many sandboxes' installed versions one process remembers. Generous
#: next to any real fleet, and an eviction costs a probe rather than a
#: reinstall -- the sandbox's own stamp is still the answer.
_REMEMBERED_SANDBOXES = 2048

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


def install_command(
    *, version: str, requires: Sequence[str], archive_sha256: str
) -> str:
    """The shell command that installs a delivered bundle.

    Module-level so the real-sandbox test runs the command this actually emits
    rather than a copy of it that can drift.

    It runs unelevated, on both fabrics. That is not luck: the images create
    `/opt/lemma-runtime` owned by the sandbox user and bake the `.pth` with
    exactly the bytes the installer would write, so the one path that needs root
    -- writing into the interpreter's own site-packages -- is never taken. Both
    were verified against live sandboxes, the second byte for byte, because a
    `.pth` differing by so much as a trailing newline would send the installer
    into a root-owned directory.

    The `sudo -n` probe stays as insurance rather than as the plan. A sandbox
    created from an image that predates the baking still has a root-owned
    `/opt/lemma-runtime`, and during a rollout those exist; where sudo is absent
    too, the install fails cleanly and the baked copy keeps serving.

    `/opt` rather than somewhere under the home, deliberately. It is where
    add-on software belongs, it keeps what the platform installed out of the
    directory listing the user browses, and it keeps the disk-copy set that a
    later migration works from as "the user's files" -- an overlay installed
    `--no-deps` against one base image has no business being carried onto
    another. The cost is that on Docker and `lemma_local`, where `/opt` is the
    container layer, replacing a container discards it and the next ensure
    reinstalls: about 650ms, once, on the fabric where a container is cheap. On
    E2B, where the sandbox is the disk, it simply persists.
    """
    return (
        "sudo -n true 2>/dev/null && SUDO='sudo -n' || SUDO=''; "
        f"$SUDO {_SANDBOX_PYTHON} {INSTALLER_PATH} install "
        f"--root {RUNTIME_ROOT} "
        f"--archive {ARCHIVE_PATH} "
        f"--version {version} "
        f"--archive-sha256 {archive_sha256} "
        f"--requires {','.join(requires)}"
    )


class _ManagerClient(Protocol):
    """The four calls this mixin makes on the workspace manager client.

    A Protocol rather than the concrete class because the mixin is mixed *into*
    that service: naming the real type here would be a cycle, and naming nothing
    left `_get_manager_client` unresolvable, so every value taken from it was
    untyped and a rename would have been silent.
    """

    async def read_file(
        self, user_id: UUID, path: str, *, deadline_at: datetime
    ) -> bytes: ...

    async def write_file(
        self,
        user_id: UUID,
        path: str,
        data: bytes,
        *,
        deadline_at: datetime,
        expected_sha256: str | None = None,
    ) -> object: ...

    async def start_process(
        self,
        kind: WorkloadKind,
        user_id: UUID,
        *,
        operation_id: UUID,
        deadline_at: datetime,
        cwd: str,
        shell_command: str,
    ) -> object: ...

    async def read_process_output(
        self,
        kind: WorkloadKind,
        user_id: UUID,
        operation_id: UUID,
        *,
        deadline_at: datetime,
        after_sequence: int,
        wait_seconds: int,
    ) -> ProcessOutputSnapshot: ...


class WorkspaceRuntimeBundleMixin:
    """``_ensure_runtime_bundle``, mixed into the workspace sandbox service."""

    def _get_manager_client(self) -> _ManagerClient:
        """Supplied by the service this is mixed into."""
        raise NotImplementedError

    #: (loop, user, sandbox, epoch, generation) -> the version known installed.
    #: Class-level, like the directory caches beside it: the answer is about a
    #: sandbox rather than about whoever happens to hold a service instance.
    #:
    #: Bounded, because the key names an allocation and allocations keep being
    #: made. Nothing releases an entry when its sandbox goes, so under sustained
    #: churn this would hold one key per sandbox the process had ever seen, for
    #: the life of the process. Evicting the oldest costs a probe -- one command
    #: -- which is the cheapest thing in this file.
    _installed_bundles: OrderedDict[tuple[int, UUID, str, int, int], str] = (
        OrderedDict()
    )
    _inflight_bundles: dict[tuple[int, UUID, str, int, int], asyncio.Task[bool]] = {}

    def _runtime_bundle(self) -> RuntimeBundle | None:
        """The bundle this service installs.

        A method rather than a module-level call at the point of use, so a test
        supplies one by overriding a seam instead of patching the name inside
        the module under test -- a double planted in the subject certifies the
        half nobody wrote and survives a rename that should have failed it.
        """
        return runtime_bundle()

    def _bundle_cache_key(
        self, user_id: UUID, sandbox_info: SandboxInfo
    ) -> tuple[int, UUID, str, int, int] | None:
        """Identity for "the overlay is installed", which belongs to the sandbox.

        Unlike a directory, the overlay does not live on a disk that outlives
        its container: it is written into the sandbox's own filesystem, which on
        Docker and `lemma_local` is the container layer. So the *epoch* has to be
        here. `allocation_id` is the logical sandbox and does not move when a
        container is replaced -- and a replacement that adopts the same volume
        keeps its files and its storage generation while losing `/opt`
        entirely. Keyed without the epoch, that sandbox reported the overlay
        installed and ran the image's older copy, which is the one outcome this
        whole mechanism exists to make impossible.

        Conservative in the only direction that is safe: a re-probe costs one
        command, a wrong "already installed" costs a sandbox running code we
        believe we replaced.
        """
        if (
            sandbox_info.allocation_id is None
            or sandbox_info.allocation_epoch is None
            or sandbox_info.storage_generation is None
        ):
            return None
        return (
            id(asyncio.get_running_loop()),
            user_id,
            sandbox_info.allocation_id,
            sandbox_info.allocation_epoch,
            sandbox_info.storage_generation,
        )

    async def _ensure_runtime_bundle(
        self, user_id: UUID, sandbox_info: SandboxInfo
    ) -> None:
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
            # A hit refreshes the entry, so the bound below evicts by last use
            # and not by when the install happened. Without this, the sandbox
            # someone has been working in all day is evicted ahead of one that
            # was installed into once and abandoned -- exactly backwards, since
            # the busy one is the whole reason this path avoids I/O.
            self._installed_bundles.move_to_end(key)
            return

        task = self._inflight_bundles.get(key) if key is not None else None
        if task is None:
            task = create_inherited_task(
                self._install_bundle(user_id, bundle),
                name=f"workspace-runtime-bundle:{user_id}",
            )
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
            self._installed_bundles.move_to_end(key)
            while len(self._installed_bundles) > _REMEMBERED_SANDBOXES:
                self._installed_bundles.popitem(last=False)

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
            component_version=bundle.component_version,
        )
        return True

    async def _installed_version(
        self, client: _ManagerClient, user_id: UUID
    ) -> str | None:
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

    async def _deliver(
        self, client: _ManagerClient, user_id: UUID, bundle: RuntimeBundle
    ) -> None:
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
            # No `expected_sha256` here, deliberately. The two providers read
            # that argument differently -- the workspace runtime treats it as a
            # precondition on the file *already* at this path, so a first upload
            # to a path with nothing at it is a 409; E2B treats it as a checksum
            # of the outgoing bytes. No single value is correct on both.
            #
            # The installer hashes the staged archive against the version
            # instead, which is stronger than either: the version *is* that
            # digest, and the check runs against the bytes that actually landed
            # rather than the ones we believe we sent.
        )

    async def _run_installer(
        self, client: _ManagerClient, user_id: UUID, bundle: RuntimeBundle
    ) -> None:
        deadline_at = _deadline(_INSTALL_BUDGET_SECONDS)
        operation_id = uuid4()
        command = install_command(
            version=bundle.version,
            requires=bundle.requires,
            archive_sha256=bundle.archive_sha256,
        )
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

    async def _await_exit(
        self,
        client: _ManagerClient,
        user_id: UUID,
        operation_id: UUID,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot | None:
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

    async def _ensure_browser_proxy(
        self, user_id: UUID, sandbox_info: SandboxInfo
    ) -> None:
        """Tell this sandbox whether its browser goes through a proxy.

        Here, beside the runtime bundle, because this is the one call that
        happens on every session -- and the viewer path is not enough on its
        own. An agent typing `agent-browser open` in its own shell reaches
        `lemma-ensure-display` without the backend in the loop, so a sandbox
        no person ever watches would never hear the decision, and an older
        one would go on using the value baked into its environment.

        Written every session rather than remembered, unlike the bundle
        above: it is one small file, the answer can change between two
        sessions, and the whole point is that withdrawing a proxy takes
        effect without anyone replacing anything.

        Failure degrades rather than propagates, like everything else in
        this mixin. A sandbox that could not be told keeps what it had --
        the state it was already in -- and the next session tries again.
        Refusing somebody a shell because a proxy file did not land would be
        the worse trade.
        """
        proxy = browser_proxy_for(_sandbox_uuid(sandbox_info), SandboxKind.WORKSPACE)
        try:
            await self._get_manager_client().write_file(
                user_id,
                BROWSER_PROXY_DECISION_PATH,
                decision_bytes(proxy),
                deadline_at=_deadline(_INSTALL_BUDGET_SECONDS),
            )
        except _SANDBOX_FAILURES:
            logger.warning(
                "workspace.browser_proxy.delivery_failed.degraded",
                user_id=str(user_id),
                exc_info=True,
            )

    @staticmethod
    def _tail(snapshot) -> str:
        """Enough of the installer's output to say what went wrong."""
        if snapshot is None or snapshot.state is ProcessState.RUNNING:
            return "no output"
        text = "".join(
            chunk.data.decode("utf-8", "replace") for chunk in snapshot.chunks
        )
        return text.strip()[-500:] or "no output"


def _sandbox_uuid(sandbox_info: SandboxInfo) -> UUID:
    """The logical sandbox id, for choosing a proxy that stays put.

    `allocation_id` moves when a container is replaced; `sandbox_id` does
    not, and stickiness across replacement is exactly the property the
    create-time mechanism never had.
    """
    return UUID(str(sandbox_info.sandbox_id))


__all__ = [
    "ARCHIVE_PATH",
    "INSTALLER_PATH",
    "RUNTIME_ROOT",
    "WorkspaceRuntimeBundleMixin",
    "install_command",
]
