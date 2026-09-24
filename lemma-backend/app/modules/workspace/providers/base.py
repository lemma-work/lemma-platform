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
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sandbox_runtime.errors import SandboxCapabilityUnsupported
from sandbox_runtime.protocol import (
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

from app.core.ports.plan_limits import SandboxSize
from app.modules.workspace.domain.sandbox import SandboxKind, SandboxMount


# Every object this module creates carries these, so a sweep can recognise its
# own work.
LABEL_MANAGED_BY = "managed-by"
LABEL_SANDBOX_ID = "lemma-sandbox-id"
LABEL_SANDBOX_KIND = "lemma-sandbox-kind"
LABEL_EPOCH = "lemma-epoch"
LABEL_PROFILE_NAME = "profile-name"
# Which build of the profile a sandbox was made from. Reuse is fenced on this:
# a sandbox is only adopted when it already runs the profile we would create it
# with, so releasing a new image actually reaches existing workspaces instead of
# leaving them on the old one for as long as they live.
LABEL_PROFILE_DIGEST = "profile-digest"
# Which stack created this, when a machine runs more than one.
#
# `managed-by=lemma-workspace` says "a Lemma sandbox", not "*my* Lemma
# sandbox", and a developer's laptop routinely has two stacks on one Docker
# daemon: the dev stack they are using, and an e2e run. A sweep filtering on
# the first label alone reaches into the other one -- which is not theoretical.
# The e2e harness deleted a live dev stack's containers and volumes mid-use,
# and the dev stack's pane went black while somebody was typing in it.
#
# Empty by default, which means unstamped and unfiltered: that is what every
# container created before this label existed looks like, and a sweep that
# suddenly stopped recognising them would leak every one of them. A stack that
# wants to be told apart sets it.
LABEL_OWNER = "lemma-owner"
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
    # What the owner's plan pays for. None is the provider's configured default.
    # Only a workspace is sized by plan; a function sandbox keeps its own.
    size: SandboxSize | None = None
    # Whether the `host.lemma.internal` alias resolves in the sandbox. Only
    # the Desktop guest acts on it. Every sandbox needs it -- the workspace
    # runtime's callbacks and the function gateway reach the backend through
    # it, via the two callback forwarders locald runs on the host gateway --
    # so it is True by default and switching it off is a deliberate,
    # per-sandbox decision. It is *not* a way onto the Mac's own loopback;
    # that is `host_loopback`. `lemma_local` sends the key only when it is
    # False: the guest parses `sandbox.ensure` with `deny_unknown_fields`, so
    # an older guest would refuse even a `True`.
    host_access: bool = True
    # Whether the sandbox gets the loopback relay: a socket through which its
    # browser reaches a port on the Mac's own `127.0.0.1` -- the owner's dev
    # server, started by host execution. True for exactly one sandbox, the
    # installation owner's own workspace (see `host_loopback_policy`); never
    # for an invited person's. Only the Desktop guest acts on it, and
    # `lemma_local` sends it only when True, for the same older-guest reason.
    host_loopback: bool = False


class ProviderStorageKind(StrEnum):
    """Where a workspace's durable files actually live.

    This is not a detail the service can paper over, because it decides what
    "the disk survived" means and therefore when a user must be told their
    files are gone.

    ``VOLUME`` -- compute and storage are separate objects. A container can be
    destroyed and replaced while the volume persists, so the epoch fences the
    container and the volume is adopted across epochs. Docker works this way.

    ``SANDBOX_NATIVE`` -- one object is both. A paused E2B sandbox keeps its
    filesystem, so resuming it *is* how storage persists; creating a second
    sandbox would leave the files in the first. Adoption therefore means
    finding and resuming the existing sandbox, and the fence is the provider's
    own id rather than an epoch in a name: a genuinely new sandbox has a new
    id, so a stale operation fails instead of landing on it.
    """

    VOLUME = "volume"
    SANDBOX_NATIVE = "sandbox_native"


@dataclass(frozen=True, slots=True)
class ProviderInstance:
    """A provider object that exists, whether or not it is ready."""

    provider_id: str
    name: str
    volume_name: str | None = None
    running: bool = False
    # Only meaningful for SANDBOX_NATIVE providers, which do their own
    # adoption inside create. False means a fresh sandbox was made, and
    # therefore that whatever files existed before are gone.
    storage_adopted: bool | None = None
    # The profile digest recorded on the object when it was created, or None
    # for one made before the fence existed. Compared against the configured
    # digest to decide whether this instance may be reused.
    profile_digest: str | None = None
    # The provider artifact this object was actually built from -- on E2B, the
    # template. Distinct from `profile_digest`, which is a hand-maintained
    # environment variable: this is the identity of the image that is running,
    # so it is the only thing that can answer "is this sandbox running the code
    # we published?". None means the object predates the stamp.
    template: str | None = None


@dataclass(frozen=True, slots=True)
class ProcessDescriptor:
    """A process the sandbox is running, as the sandbox reports it.

    Addressed by the operation id the caller supplied, because that is the
    handle the runtime keys on and the only one that survives the backend
    rebuilding its client between tool calls.
    """

    process_id: str
    state: object
    exit_code: int | None = None
    started_at: datetime | None = None
    # What the process is and where it runs. A sandbox belongs to a user, not
    # to a conversation, so a listing without these is a column of opaque uuids
    # spanning every session that user has open -- and the tool docstring sends
    # agents to that listing to recover a process they have lost the id for.
    cwd: str = ""
    command: str = ""
    tty: bool = False


@dataclass(frozen=True, slots=True)
class ProviderObject:
    """One object found by a sweep, with enough labels to judge it."""

    provider_id: str
    name: str
    sandbox_id: UUID | None
    epoch: int | None
    running: bool
    legacy: bool = False
    # Compute or disk. They are judged differently and must be: a container
    # belongs to one epoch and is superseded by the next, while a volume IS the
    # workspace's disk and deliberately outlives every container that mounts it.
    # Reclaiming a volume on an epoch bump would delete a live workspace.
    kind: str = "container"
    # Which disk generation this volume holds. None for containers, and for a
    # volume whose name predates generational naming -- unparseable means "not
    # judgeable by generation", never "generation zero".
    storage_generation: int | None = None


class ProviderCapability(StrEnum):
    """What a fabric can do beyond running processes and moving files.

    Declared rather than discovered. A caller that needs one asks first and gets
    a sentence back when the answer is no; the alternative -- calling and seeing
    what breaks -- is how a method implemented for one provider came to raise
    `AttributeError` on the two that carry production traffic.
    """

    #: A port inside the sandbox can be reached from the API.
    PORT_REACH = "port_reach"
    #: A file can be placed inside the sandbox without going through a shell,
    #: so a credential never appears in argv or in the environment.
    SECRET_DELIVERY = "secret_delivery"


@dataclass(frozen=True, slots=True)
class SandboxEndpoint:
    """Where to reach something inside a sandbox, and what it takes to get in.

    The headers are the fabric's own doorkeeper, not the sandbox's: E2B wants a
    traffic token, a preview proxy wants its own, Docker and a pod IP want
    nothing. Returning them beside the URL is what lets the proxy and the
    browser bridge stay ignorant of which fabric they are talking to -- the
    alternative is every caller learning the provider's name and its header.

    `public` is the honest half. An E2B host is on the internet whether or not
    we like it, and a caller that is about to expose a port needs to know that
    the only thing in front of it is the token.
    """

    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    public: bool = False


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
    # Decides whether the service manages a separate disk for this provider or
    # leaves storage to the provider's own adoption. See ProviderStorageKind.
    storage_kind: ProviderStorageKind
    # Can a stopped instance be brought back where it is, or does it have to be
    # rebuilt?
    #
    # Docker starts a stopped container; E2B resumes a paused sandbox; both do
    # it inside `wait_ready`, so the service only has to wait. A provider that
    # cannot -- the desktop guest has no start, only create-or-replace -- says
    # so here, and the service rebuilds instead of waiting for something that
    # is never coming back on its own. Getting this wrong is not a slow path:
    # it is an ensure that fails identically until its deadline, because every
    # retry takes the same branch and nothing in it starts anything.
    #
    # Rebuilding keeps the sandbox's files. The volume is resolved from the
    # sandbox, not the instance, so a new epoch mounts the same disk.
    resumes_stopped_instances: bool = True

    async def create(self, spec: ProviderCreateSpec) -> ProviderInstance:
        """Materialise the sandbox this spec names, or adopt the existing one."""

    async def wait_ready(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Return once the sandbox is serving, or raise saying why it is not."""

    async def inspect(
        self, name: str, *, deadline_at: datetime
    ) -> ProviderInstance | None:
        """The instance behind this name, or None when nothing holds it."""

    async def release(
        self, instance: ProviderInstance, *, kind: SandboxKind, deadline_at: datetime
    ) -> None:
        """Stop compute, keep the disk. The sandbox can be resumed."""

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        """Remove the object this name refers to. Already-gone is success."""

    async def find_volume(
        self, *, sandbox_id: UUID, deadline_at: datetime
    ) -> str | None:
        """Locate an existing volume for this sandbox, including a legacy one."""

    async def destroy_volume(self, name: str, *, deadline_at: datetime) -> None:
        """Remove a volume by name. Already-gone is success."""

    async def list_objects(
        self, *, deadline_at: datetime
    ) -> tuple[ProviderObject, ...]:
        """Every sandbox object this provider holds, for orphan reclamation."""

    async def close(self) -> None:
        """Release any transport this provider holds."""


def resumes_stopped_instances(provider: object) -> bool:
    """Can this provider bring a stopped instance back where it is?

    Read structurally rather than off the Protocol: a provider that does not
    declare it resumes, which is what every provider did before the capability
    existed.
    """
    return bool(getattr(provider, "resumes_stopped_instances", True))


def provider_name_for(provider: object, sandbox_id: UUID) -> str:
    """The fabric this sandbox runs on, for the instance row and the handle.

    A provider that fronts several (``HostRoutingProvider``) answers per
    sandbox; every other provider is one fabric and answers with its name.
    """
    name_for = getattr(provider, "name_for", None)
    if callable(name_for):
        return str(name_for(sandbox_id))
    return str(getattr(provider, "name"))


def storage_kind_for(provider: object, sandbox_id: UUID) -> ProviderStorageKind:
    """Where this sandbox's files live; see ``ProviderStorageKind``."""
    kind_for = getattr(provider, "storage_kind_for", None)
    if callable(kind_for):
        return kind_for(sandbox_id)
    return getattr(provider, "storage_kind", ProviderStorageKind.VOLUME)


def require_capability(provider: object, capability: ProviderCapability) -> None:
    """Refuse, in words, before calling something this fabric cannot do.

    A provider that does not declare a capability is treated as not having it,
    so a fabric added later is refused honestly until it says otherwise. That is
    the opposite of the previous arrangement, where the absence of a method
    surfaced as `AttributeError` from inside a request handler.
    """
    declared = getattr(provider, "capabilities", frozenset())
    if capability in declared:
        return
    raise SandboxCapabilityUnsupported(
        str(capability),
        kind=getattr(provider, "provider_name", type(provider).__name__),
    )


class SandboxOpsProvider(Protocol):
    """Operations inside a running sandbox.

    Separate from lifecycle because not every provider offers them: a function
    runtime is reachable only on its port, and a future provider that only
    rents compute would implement lifecycle alone.
    """

    async def start_process(
        self,
        instance: ProviderInstance,
        request: StartProcessRequest,
        *,
        deadline_at: datetime,
    ) -> str:
        """Begin a process and return the id its output is read by."""

    async def read_process_output(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        after_sequence: int,
        wait_seconds: float,
        deadline_at: datetime,
    ) -> ProcessOutputSnapshot:
        """Output after `after_sequence`, which is exclusive and 1-based."""

    async def send_process_input(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        data: bytes,
        deadline_at: datetime,
    ) -> None:
        """Write to the process's stdin, or its PTY when it has one."""

    async def resize_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        size: TerminalSize,
        deadline_at: datetime,
    ) -> None:
        """Tell a PTY-backed process its terminal changed size."""

    async def terminate_process(
        self,
        instance: ProviderInstance,
        *,
        process_id: str,
        grace_seconds: float,
        deadline_at: datetime,
    ) -> None:
        """Signal the process, escalating once the grace period lapses."""

    async def list_processes(
        self, instance: ProviderInstance, *, deadline_at: datetime
    ) -> tuple[ProcessDescriptor, ...]:
        """Every process the sandbox is currently tracking."""

    async def stat_file(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> FileStat:
        """Metadata for one path, raising when it does not exist."""

    async def list_files(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> tuple[FileStat, ...]:
        """The direct children of a directory."""

    async def create_directory(
        self, instance: ProviderInstance, *, path: str, deadline_at: datetime
    ) -> None:
        """Create a directory and any missing parents. Idempotent."""

    async def open_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        byte_range: ByteRange,
        deadline_at: datetime,
    ) -> AsyncIterator[bytes]:
        """Stream a byte range of a file."""

    async def write_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        data: AsyncIterable[bytes],
        expected_sha256: str | None,
        deadline_at: datetime,
    ) -> FileStat:
        """Write a stream to a path, verifying the digest when one is given.

        ``expected_sha256`` is the prefixed form, ``sha256:<64 hex>`` -- the same
        spelling `FileStat.sha256` carries. The workspace runtime validates that
        pattern on the wire and answers 422 to a bare digest, while E2B strips
        the prefix itself and accepts either, so a caller passing the bare form
        works on one fabric and not the others.
        """

    async def move_file(
        self,
        instance: ProviderInstance,
        *,
        source: str,
        destination: str,
        deadline_at: datetime,
    ) -> None:
        """Rename within the sandbox."""

    async def delete_file(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        recursive: bool,
        deadline_at: datetime,
    ) -> bool:
        """Remove a path, reporting whether anything was there to remove."""

    async def ensure_python_session(
        self, instance: ProviderInstance, request: CreatePythonSessionRequest
    ) -> None:
        """Make a persistent Python session exist, keeping its namespace."""

    async def execute_python(
        self,
        instance: ProviderInstance,
        session: PythonSessionRef,
        request: ExecutePythonRequest,
    ) -> PythonResult:
        """Run a fragment in a session and return what it produced."""

    async def delete_python_session(
        self, instance: ProviderInstance, *, session_id: str, deadline_at: datetime
    ) -> None:
        """Discard a session and the namespace it held."""

    # ------------------------------------------------------------------
    # Reaching in
    # ------------------------------------------------------------------

    #: What this fabric offers beyond processes and files. Declared as a plain
    #: attribute so `require_capability` can ask without calling anything.
    capabilities: frozenset[ProviderCapability]

    async def reach_port(
        self, instance: ProviderInstance, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        """Where a port inside this sandbox is answered, and with what.

        Replaces the `port_base_url` every provider grew privately: that
        returned a bare string, so a fabric whose door needs a token had nowhere
        to put it and each caller had to know which fabric it was on.

        Raises `SandboxCapabilityUnsupported` where ports are not reachable at
        all -- a real answer, and one a caller can render as a sentence rather
        than as a stack trace.
        """

    async def deliver_secret(
        self,
        instance: ProviderInstance,
        *,
        path: str,
        value: bytes,
        deadline_at: datetime,
    ) -> None:
        """Place bytes at a path inside the sandbox, readable only by its owner.

        For credentials, which must not travel as argv (any process can read
        `/proc/<pid>/cmdline`) or as environment (inherited by everything the
        sandbox starts). Idempotent: delivering the same secret twice is how a
        resumed sandbox gets its token back.
        """
