"""The provider seam: what it takes to rent a sandbox from some compute fabric.

Kept deliberately narrow. A provider creates, starts, stops, destroys, and
enumerates compute; everything about *which* sandbox, *when*, and *why* is the
service's business.

This is the seam a remote Docker endpoint or a Kubernetes cluster plugs into,
which is why it speaks in names and labels rather than in allocations: a
Deployment, a container, and an E2B sandbox all have a name you choose and
labels you can query, and that is enough to own them.

The shape it replaces carried allocation ids, allocation tokens, admission
classes, and a create-attempt ledger, because a create whose response was lost
could not be distinguished from one that never happened. Deterministic naming
removes that question -- retrying a create either creates the name or finds it
already there -- so the ledger, the reconciler, and the ambiguity vocabulary
around create all go away. What remains is `ProviderCreateAmbiguous`, for the
genuinely unresolvable case where the provider cannot even be asked.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from agentbox.domain import (
    ByteRange,
    CreatePythonSessionRequest,
    ExecutePythonRequest,
    FileStat,
    ProcessOutputSnapshot,
    PythonResult,
    PythonSessionRef,
    StartProcessRequest,
    TerminalSize,
)

from app.modules.workspace.domain.sandbox import SandboxKind, SandboxMount


# Every object this module creates carries these, so a sweep can recognise its
# own work. `managed-by` is matched permissively on read: pre-consolidation
# objects carry "agentbox" and must still be found, or they leak.
LABEL_MANAGED_BY = "managed-by"
LABEL_SANDBOX_ID = "lemma-sandbox-id"
LABEL_SANDBOX_KIND = "lemma-sandbox-kind"
LABEL_EPOCH = "lemma-epoch"
# What AgentBox stamped on the same objects. Read-only compatibility.
LEGACY_MANAGED_BY = "agentbox"
LEGACY_LOGICAL_ID = "logical-id"
MANAGED_BY = "lemma-workspace"


@dataclass(frozen=True, slots=True)
class ProviderCreateSpec:
    """Everything a provider needs to materialise one sandbox instance."""

    sandbox_id: UUID
    kind: SandboxKind
    epoch: int
    # Chosen by the service, not the provider, because it is the fence: an
    # operation naming an old epoch must not resolve to a current container.
    name: str
    image: str
    profile_name: str
    profile_digest: str
    deadline_at: datetime
    volume_name: str | None = None
    mounts: tuple[SandboxMount, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderInstance:
    """A provider object that exists, whether or not it is ready."""

    provider_id: str
    name: str
    volume_name: str | None = None
    running: bool = False


@dataclass(frozen=True, slots=True)
class ProviderObject:
    """One object found by a sweep, with enough labels to judge it."""

    provider_id: str
    name: str
    sandbox_id: UUID | None
    epoch: int | None
    running: bool
    legacy: bool = False


class ProviderCreateAmbiguous(RuntimeError):
    """The provider could not be asked, so create may or may not have landed.

    Rare by construction: with deterministic names, the recovery is to inspect
    the name rather than to reconcile a ledger.
    """


class ProviderRejected(RuntimeError):
    """The provider definitively refused. Retrying as-is cannot help."""


class ProviderNotReady(RuntimeError):
    """The object exists but is not usable yet."""

    def __init__(self, message: str, *, retry_after_ms: int = 250) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class ProviderFailed(RuntimeError):
    """The object exists and cannot become usable."""


class ProviderGone(RuntimeError):
    """The object an operation named no longer exists.

    Raised when a stale epoch is used. It is definitive rather than retryable:
    the caller must re-ensure to get a current handle, not retry the same call.
    """


class SandboxProvider(Protocol):
    """Lifecycle over some compute fabric."""

    name: str

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance: ...

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None: ...

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None: ...

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Stop compute, keep the disk. The sandbox can be resumed."""

    async def destroy(self, name: str, *, deadline_at: datetime) -> None: ...

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        """Locate an existing volume for this sandbox, including a legacy one."""

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None: ...

    async def list_objects(self, *, deadline_at: datetime) -> tuple[ProviderObject, ...]:
        """Every sandbox object this provider holds, for orphan reclamation."""

    async def close(self) -> None: ...


class SandboxOpsProvider(Protocol):
    """Operations inside a running sandbox.

    Separate from lifecycle because not every provider offers them: a function
    runtime is reachable only on its port, and a future provider that only
    rents compute would implement lifecycle alone.
    """

    async def start_process(
        self, instance: ProviderInstance, request: StartProcessRequest, *,
        deadline_at: datetime,
    ) -> str: ...

    async def read_process_output(
        self, instance: ProviderInstance, *, process_id: str, after_sequence: int,
        wait_seconds: float, deadline_at: datetime,
    ) -> ProcessOutputSnapshot: ...

    async def send_process_input(
        self, instance: ProviderInstance, *, process_id: str, data: bytes,
        deadline_at: datetime,
    ) -> None: ...

    async def resize_process(
        self, instance: ProviderInstance, *, process_id: str, size: TerminalSize,
        deadline_at: datetime,
    ) -> None: ...

    async def terminate_process(
        self, instance: ProviderInstance, *, process_id: str, grace_seconds: float,
        deadline_at: datetime,
    ) -> None: ...

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat: ...

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]: ...

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None: ...

    async def open_file(
        self, instance: ProviderInstance, *, path: str, byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]: ...

    async def write_file(
        self, instance: ProviderInstance, *, path: str, data: AsyncIterable[bytes],
        expected_sha256: str | None, deadline_at: datetime,
    ) -> FileStat: ...

    async def move_file(
        self, instance: ProviderInstance, *, source: str, destination: str,
        deadline_at: datetime,
    ) -> None: ...

    async def delete_file(
        self, instance: ProviderInstance, *, path: str, recursive: bool,
        deadline_at: datetime,
    ) -> bool: ...

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None: ...

    async def execute_python(
        self, instance: ProviderInstance, session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult: ...

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None: ...
