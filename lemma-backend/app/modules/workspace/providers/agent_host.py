"""A sandbox whose compute is a folder on the installation owner's Mac.

See docs/architecture/desktop-host-execution.md. Every operation is one ``op``
request to the owner's Agent Host, which runs it inside ``lemma-agent-host
exec-server`` under the Seatbelt profile. This module never touches the Mac
itself; it speaks the op vocabulary (§4) and turns the host's refusals into the
``sandbox_runtime`` errors every other fabric raises, so the session and the
tools above it do not know which fabric they are on.

Lifecycle is small because there is nothing to provision. ``create`` is
``workspace.open`` -- the host picks and creates the root (§5) and answers with
its absolute path, which is recorded on the binding. ``release`` and
``destroy`` are ``workspace.close``. There are no volumes and nothing for an
orphan sweep to find: the files are the owner's, in the owner's folder, and
outlive every sandbox that was ever opened on them.

A host that restarted has forgotten which workspaces were open. Its answer to
the next op is ``workspace_not_open``, and the provider re-opens from the
binding and tries once more, so a restart costs one round trip rather than a
failed tool call.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sandbox_runtime.errors import (
    SandboxOperationAmbiguous,
    SandboxPathConflict,
    SandboxPathNotFound,
    SandboxProcessNotFound,
    SandboxRejected,
    SandboxUnavailable,
)

from app.core.log.log import get_logger
from app.modules.workspace.domain.host_execution import (
    HOST_EXECUTION_PROVIDER,
    HostBinding,
)
from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers import naming
from app.modules.workspace.providers.agent_host_ops import AgentHostOpsMixin
from app.modules.workspace.providers.base import (
    ProviderCreateSpec,
    ProviderInstance,
    ProviderObject,
    ProviderRejected,
    ProviderStorageKind,
)

logger = get_logger(__name__)

#: ``kind`` values raised on the Lemma side of the link rather than by the host.
HOST_OFFLINE = "host_offline"
WORKSPACE_NOT_OPEN = "workspace_not_open"

#: How long lifecycle ops may take. ``workspace.open`` makes a directory.
_LIFECYCLE_SECONDS = 30.0


class HostOpRefused(Exception):
    """An op that produced no result, in this module's vocabulary.

    The composition root adapts the link client's own error into this one, so
    this module depends on a shape rather than on the agent module.
    """

    def __init__(self, kind: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable


class HostOpTransport(Protocol):
    async def request(
        self,
        *,
        host_id: UUID,
        workspace: UUID,
        method: str,
        params: dict[str, object],
        deadline_at: datetime,
    ) -> dict[str, object]: ...


class HostBindingStore(Protocol):
    async def get(self, sandbox_id: UUID) -> HostBinding | None: ...

    async def set_root(self, sandbox_id: UUID, root: str) -> None: ...


_CONFLICT_KINDS = frozenset(
    {"already_exists", "not_a_directory", "is_a_directory", "digest_mismatch"}
)
_AMBIGUOUS_KINDS = frozenset({"timeout", "link_lost"})
_TRANSIENT_KINDS = frozenset(
    {"exec_server_unavailable", "link_unavailable", WORKSPACE_NOT_OPEN}
)
_SENTENCES = {
    "outside_workspace": (
        "That path is outside this conversation's folder on the Mac. File "
        "operations here reach only the working directory, the temporary "
        "directory and folders the owner granted."
    ),
    "permission_denied": (
        "The Mac refused that: the path is not writable from here, or the "
        "sandbox protecting the owner's credentials denied it."
    ),
    "exec_server_unavailable": (
        "The command runner on the Mac is restarting. Try again in a moment."
    ),
    "too_large": "That is more than the Mac will move in one operation.",
}


def sandbox_error(exc: HostOpRefused, *, method: str) -> Exception:
    """The ``sandbox_runtime`` error an op refusal means, worded for an agent."""
    message = _SENTENCES.get(exc.kind) or exc.message or f"{method} failed"
    if exc.kind == "not_found":
        return SandboxPathNotFound(message)
    if exc.kind == "process_not_found":
        return SandboxProcessNotFound(message)
    if exc.kind in _CONFLICT_KINDS:
        return SandboxPathConflict(message)
    if exc.kind in _AMBIGUOUS_KINDS:
        return SandboxOperationAmbiguous(
            f"{message} It may or may not have taken effect on the Mac."
        )
    if exc.kind in _TRANSIENT_KINDS or exc.retryable:
        return SandboxUnavailable(message, retry_after_ms=500)
    # Offline is definitive for this call: waiting out a sleeping Mac inside a
    # tool call helps nobody, and the sentence already says what to do.
    return SandboxRejected(message)


def _deadline(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class AgentHostSandboxProvider(AgentHostOpsMixin):
    """``SandboxProvider`` and ``SandboxOpsProvider`` over the Agent Host link."""

    name = HOST_EXECUTION_PROVIDER
    provider_name = "Host"
    storage_kind = ProviderStorageKind.SANDBOX_NATIVE
    resumes_stopped_instances = True

    def __init__(self, transport: HostOpTransport, bindings: HostBindingStore) -> None:
        self._transport = transport
        self._bindings = bindings

    # ------------------------------------------------------------ transport

    async def _binding(self, sandbox_id: UUID) -> HostBinding:
        binding = await self._bindings.get(sandbox_id)
        if binding is None:
            raise SandboxRejected(
                "This conversation has no Mac recorded to run on, so the "
                "command did not run."
            )
        return binding

    async def _open(self, binding: HostBinding, *, deadline_at: datetime) -> str:
        result = await self._transport.request(
            host_id=binding.host_id,
            workspace=binding.sandbox_id,
            method="workspace.open",
            params={
                "root_hint": binding.root_hint,
                "grants": [],
                "conversation_id": str(binding.conversation_id),
                # The conversation's own date, not today's, so a reopen on
                # another day finds the same folder.
                "date": binding.day,
                "slug": binding.slug,
            },
            deadline_at=deadline_at,
        )
        root = result.get("root")
        if not isinstance(root, str) or not root.startswith("/"):
            raise ProviderRejected("the Mac did not say which folder it opened")
        if root != binding.root:
            await self._bindings.set_root(binding.sandbox_id, root)
        return root

    async def open_workspace(self, sandbox_id: UUID, *, deadline_at: datetime) -> str:
        """Open (or re-open) a host sandbox and return the root the Mac chose."""
        binding = await self._binding(sandbox_id)
        try:
            return await self._open(binding, deadline_at=deadline_at)
        except HostOpRefused as exc:
            raise sandbox_error(exc, method="workspace.open") from exc

    async def op(
        self,
        sandbox_id: UUID,
        method: str,
        params: dict[str, object],
        *,
        deadline_at: datetime,
    ) -> dict[str, object]:
        """One op on this sandbox's host, re-opening once if it forgot us."""
        binding = await self._binding(sandbox_id)
        for attempt in range(2):
            try:
                return await self._transport.request(
                    host_id=binding.host_id,
                    workspace=sandbox_id,
                    method=method,
                    params=params,
                    deadline_at=deadline_at,
                )
            except HostOpRefused as exc:
                if exc.kind != WORKSPACE_NOT_OPEN or attempt:
                    raise sandbox_error(exc, method=method) from exc
                logger.info(
                    "workspace.agent_host_provider.reopened",
                    sandbox_id=str(sandbox_id),
                    method=method,
                )
                try:
                    await self._open(binding, deadline_at=deadline_at)
                except HostOpRefused as reopen:
                    raise sandbox_error(reopen, method="workspace.open") from reopen
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def _instance_op(
        self,
        instance: ProviderInstance,
        method: str,
        params: dict[str, object],
        *,
        deadline_at: datetime,
    ) -> dict[str, object]:
        return await self.op(
            _sandbox_id(instance.name), method, params, deadline_at=deadline_at
        )

    # ------------------------------------------------------------ lifecycle

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        binding = await self._bindings.get(spec.sandbox_id)
        if binding is None:
            raise ProviderRejected("no Mac is recorded for this host sandbox")
        try:
            await self._open(binding, deadline_at=spec.deadline_at)
        except HostOpRefused as exc:
            raise ProviderRejected(
                str(sandbox_error(exc, method="workspace.open"))
            ) from exc
        return ProviderInstance(provider_id=spec.name, name=spec.name, running=True)

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Open is synchronous: a workspace the host answered for is ready."""
        del instance, kind, deadline_at

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        """Always there, without asking the Mac.

        Asking would cost every ensure a round trip, and would not prove more
        than the next op does: a Mac that went away answers that op with the
        sentence the agent needs, and one that restarted is re-opened by it.
        """
        del deadline_at
        return ProviderInstance(provider_id=name, name=name, running=True)

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        del kind
        await self._close(instance.name, deadline_at=deadline_at)

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        await self._close(name, deadline_at=deadline_at)

    async def _close(self, name: str, *, deadline_at: datetime) -> None:
        """Forget the workspace on the Mac. The folder and its files stay.

        A Mac that is not connected has nothing open to close, so offline is
        success here -- the same as "already gone" for every other fabric.
        """
        sandbox_id = _sandbox_id(name)
        binding = await self._bindings.get(sandbox_id)
        if binding is None:
            return
        try:
            await self._transport.request(
                host_id=binding.host_id,
                workspace=sandbox_id,
                method="workspace.close",
                params={},
                deadline_at=min(deadline_at, _deadline(_LIFECYCLE_SECONDS)),
            )
        except HostOpRefused as exc:
            if exc.kind in {HOST_OFFLINE, WORKSPACE_NOT_OPEN}:
                return
            raise ProviderRejected(
                str(sandbox_error(exc, method="workspace.close"))
            ) from exc

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        del sandbox_id, deadline_at
        return None

    async def ensure_volume(
        self, *, sandbox_id: UUID, name: str, deadline_at: datetime
    ) -> str | None:
        del sandbox_id, name, deadline_at
        return None

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        del name, deadline_at

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """Nothing to reclaim: an orphaned host workspace is just a folder."""
        del deadline_at
        return ()

    async def close(self) -> None:
        return None


def _sandbox_id(name: str) -> UUID:
    parsed = naming.parse_container_name(name)
    if parsed is None:
        raise ProviderRejected(f"{name!r} is not a host sandbox name")
    return parsed[0]
