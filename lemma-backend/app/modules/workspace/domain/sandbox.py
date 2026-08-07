"""Sandbox domain: the provisioning primitive the workspace module owns.

A sandbox is one addressable, provisioned compute environment with a lifecycle.
Two kinds share that primitive because they share every lifecycle column and
every state transition; only the image and the capability set differ:

- ``WORKSPACE`` -- one per named workspace, owned by a user. Holds a durable
  volume with the user's files.
- ``FUNCTION`` -- one per pod, owned by that pod. A resident function runtime,
  reachable only on its port. No durable storage: a wiped function sandbox has
  lost nothing, because the artifact is refetched from the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class SandboxKind(StrEnum):
    WORKSPACE = "workspace"
    FUNCTION = "function"


class SandboxOwnerKind(StrEnum):
    USER = "user"
    POD = "pod"


class SandboxDesiredState(StrEnum):
    """What the operator wants, independent of what the provider currently has."""

    PRESENT = "present"
    RELEASED = "released"
    DELETED = "deleted"


class SandboxInstanceState(StrEnum):
    """Where one concrete provider object is in its life."""

    CREATING = "creating"
    READY = "ready"
    RELEASED = "released"
    DESTROYED = "destroyed"
    ERROR = "error"


class SandboxCapability(StrEnum):
    """Declared per kind, checked before an operation is attempted.

    Carried on the handle rather than inferred from the kind at each call site,
    so a caller gets a typed refusal naming the capability instead of an
    AttributeError or a provider-specific 500.
    """

    PROCESSES = "processes"
    PTY = "pty"
    PYTHON_SESSIONS = "python_sessions"
    FILESYSTEM = "filesystem"
    PORT_ACCESS = "port_access"
    DURABLE_STORAGE = "durable_storage"


WORKSPACE_CAPABILITIES = frozenset(
    {
        SandboxCapability.PROCESSES,
        SandboxCapability.PTY,
        SandboxCapability.PYTHON_SESSIONS,
        SandboxCapability.FILESYSTEM,
        SandboxCapability.PORT_ACCESS,
        SandboxCapability.DURABLE_STORAGE,
    }
)

# A function sandbox is deliberately reachable only on its port. It runs an
# immutable artifact; giving it a shell or a filesystem would make the revision
# digest a lie.
FUNCTION_CAPABILITIES = frozenset({SandboxCapability.PORT_ACCESS})


def capabilities_for(kind: SandboxKind) -> frozenset[SandboxCapability]:
    return (
        WORKSPACE_CAPABILITIES if kind is SandboxKind.WORKSPACE else FUNCTION_CAPABILITIES
    )


DEFAULT_SLUG = "default"
WORKSPACE_ROOT = "/workspace"


@dataclass(frozen=True, slots=True)
class Sandbox:
    """A durable sandbox row."""

    id: UUID
    kind: SandboxKind
    owner_kind: SandboxOwnerKind
    owner_id: UUID
    slug: str
    display_name: str
    profile_name: str
    profile_digest: str
    desired_state: SandboxDesiredState
    # Bumped on every (re)create. Stamped into the container name, so a stale
    # operation addresses a container that does not exist rather than landing
    # on a replacement.
    epoch: int
    # Bumped only when the durable disk is replaced. This is how an agent tells
    # "your files are gone" from "this directory happens to be empty".
    storage_generation: int
    # Adopted, never derived. The pre-consolidation name is
    # ab-ws-{random uuid4}, so it cannot be reconstructed from any id here.
    provider_volume_id: str | None = None
    mounts: tuple[SandboxMount, ...] = ()
    last_used_at: datetime | None = None
    delete_after: datetime | None = None

    @property
    def capabilities(self) -> frozenset[SandboxCapability]:
        return capabilities_for(self.kind)

    def has(self, capability: SandboxCapability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class SandboxMount:
    """A host path bound into the sandbox. Local deployments only."""

    host_path: str
    container_path: str
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class SandboxInstance:
    """One concrete provider object backing a sandbox at a given epoch."""

    id: UUID
    sandbox_id: UUID
    epoch: int
    provider: str
    state: SandboxInstanceState
    provider_id: str | None = None
    provider_volume_id: str | None = None
    last_error: str | None = None
    ready_at: datetime | None = None
    released_at: datetime | None = None
    # When the row was written, which is when provisioning was claimed. Used to
    # tell a claim someone is still working on from one whose owner died.
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """A ready sandbox, addressed at a specific epoch.

    Every operation carries the handle so the epoch travels with it. Holding a
    handle across a suspend/recreate is safe: the operation targets a container
    name that no longer resolves, which is a definitive "gone, re-ensure"
    rather than a silent write into a replacement.
    """

    sandbox_id: UUID
    kind: SandboxKind
    epoch: int
    provider: str
    provider_id: str
    capabilities: frozenset[SandboxCapability] = field(default_factory=frozenset)
    storage_generation: int = 1
    root_path: str = WORKSPACE_ROOT

    def has(self, capability: SandboxCapability) -> bool:
        return capability in self.capabilities
