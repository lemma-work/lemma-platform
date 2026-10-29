"""Machines: the sandboxes a person or a pod addresses by name.

A machine is a ``sandboxes`` row of kind ``workspace`` seen from the outside.
The row keeps its lifecycle (epoch, storage generation, instances); this adds
what a caller chooses and what a caller is told: a template and a size, the
fabric it runs on, the capabilities it offers, and who may reach it.

Three kinds, and the difference is ownership, not machinery:

- the **default machine**, one per user, whose row id is the user id -- the
  equality every existing workspace caller already relies on;
- **pod machines**, owned by a pod and shared by its agents, visible to the
  whole pod or only to those granted it;
- **connected machines**, a person's own laptop or VM joined over a link.

Refs are how every caller names one: ``me``, ``pod:<pod>/<name>``,
``mine:<name>`` or the row id. ``services/machine_resolver.py`` is the only
thing that turns a ref into a row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.core.ports.plan_limits import SandboxSize
from sandbox_runtime.paths import HOME_ROOT


class MachineKind(StrEnum):
    DEFAULT = "default"
    POD = "pod"
    CONNECTED = "connected"


class MachineVisibility(StrEnum):
    """Who in a pod sees a pod machine.

    ``PRIVATE`` is a person's own machine: their default, or one they
    connected and did not share. ``RESTRICTED`` exists for isolation, not
    tidiness: a verifier's machine the builder must not be able to reach.
    """

    PRIVATE = "private"
    POD = "pod"
    RESTRICTED = "restricted"


class MachineBillingScope(StrEnum):
    """Whose compute window a machine's time is counted in."""

    USER = "user"
    ORG = "org"


class MachineFabric(StrEnum):
    E2B = "e2b"
    DOCKER = "docker"
    LEMMA_LOCAL = "lemma_local"
    AGENT_HOST = "agent_host"
    CONNECTED = "connected"


class MachineTemplate(StrEnum):
    """What a machine is built from. Docker only exists on its own template."""

    WORKSPACE = "workspace"
    WORKSPACE_DOCKER = "workspace-docker"


@dataclass(frozen=True, slots=True)
class MachineSize:
    """``<vcpu>x<memory GB>``, the unit sizes are sold and templates are built in."""

    cpu_count: int
    memory_gb: int

    @property
    def label(self) -> str:
        return f"{self.cpu_count}x{self.memory_gb}"

    @property
    def sandbox_size(self) -> SandboxSize:
        return SandboxSize(cpu_count=self.cpu_count, memory_mb=self.memory_gb * 1024)

    @property
    def compute_units_per_hour(self) -> float:
        """1 CU is one hour of the 2x4 default; other sizes scale by vCPU."""
        return self.cpu_count / DEFAULT_MACHINE_SIZE.cpu_count

    @classmethod
    def parse(cls, label: str) -> MachineSize:
        text = label.strip().lower()
        # Every real label is a handful of characters ("16x64"); bounding it
        # first keeps the match linear whatever a caller sends.
        match = _SIZE_LABEL.fullmatch(text) if len(text) <= _MAX_LABEL else None
        if match is None:
            raise ValueError(f"'{label}' is not a machine size; use e.g. 2x4")
        size = cls(int(match.group(1)), int(match.group(2)))
        if size not in KNOWN_SIZES:
            known = ", ".join(s.label for s in KNOWN_SIZES)
            raise ValueError(f"'{label}' is not an offered size ({known})")
        return size

    @classmethod
    def from_sandbox_size(cls, size: SandboxSize) -> MachineSize:
        return cls(size.cpu_count, max(1, size.memory_mb // 1024))


_SIZE_LABEL = re.compile(r"(\d{1,3})x(\d{1,4})")
_MAX_LABEL = 8

#: Every size a template is built at. A plan narrows this; nothing widens it.
KNOWN_SIZES = (
    MachineSize(1, 2),
    MachineSize(2, 4),
    MachineSize(4, 8),
    MachineSize(8, 16),
)
DEFAULT_MACHINE_SIZE = MachineSize(2, 4)
#: The default machine's size before the 2x4 migration; still what an
#: unmigrated row reports.
LEGACY_DEFAULT_SIZE = MachineSize(1, 2)

#: Sizes each template is built at. Docker needs room for a compose stack.
TEMPLATE_SIZES: dict[MachineTemplate, tuple[MachineSize, ...]] = {
    MachineTemplate.WORKSPACE: KNOWN_SIZES,
    MachineTemplate.WORKSPACE_DOCKER: (MachineSize(4, 8), MachineSize(8, 16)),
}


#: Fabrics that can build a template other than the plain workspace. Docker
#: inside a machine needs a VM of its own -- E2B's -- and a Docker fabric's
#: container cannot run dockerd safely, so on Docker it is not offered at all:
#: a `workspace-docker` machine there was created, said `docker: true`, and had
#: no Docker in it.
_TEMPLATE_FABRICS: dict[MachineTemplate, frozenset[MachineFabric]] = {
    MachineTemplate.WORKSPACE_DOCKER: frozenset({MachineFabric.E2B}),
}


def template_builds_on(template: MachineTemplate, fabric: MachineFabric) -> bool:
    """Whether this deployment's fabric can build machines from `template`."""
    fabrics = _TEMPLATE_FABRICS.get(template)
    return fabrics is None or fabric in fabrics


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a machine can do, declared per machine and checked before a call.

    Stored on the row (``capabilities`` jsonb) because a connected machine
    reports its own; for the others it follows from the template.
    """

    processes: bool = True
    pty: bool = True
    python: bool = True
    files: bool = True
    previews: bool = True
    browser: bool = True
    docker: bool = False

    def to_json(self) -> dict[str, bool]:
        return {
            "processes": self.processes,
            "pty": self.pty,
            "python": self.python,
            "files": self.files,
            "previews": self.previews,
            "browser": self.browser,
            "docker": self.docker,
        }

    @classmethod
    def from_json(cls, value: dict[str, bool] | None) -> Capabilities:
        if not value:
            return cls()
        known = {
            name: bool(v) for name, v in value.items() if name in _CAPABILITY_NAMES
        }
        return cls(**known)

    @classmethod
    def for_template(cls, template: MachineTemplate) -> Capabilities:
        return cls(docker=template is MachineTemplate.WORKSPACE_DOCKER)


_CAPABILITY_NAMES = frozenset(Capabilities().to_json())

#: Machine names inside a pod: short, lower-case, usable in a ref and a URL.
MACHINE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")


def validate_machine_name(name: str) -> str:
    if not MACHINE_NAME.fullmatch(name):
        raise ValueError(
            "a machine name is 1-40 lower-case letters, digits and dashes, "
            "starting and ending with a letter or digit"
        )
    return name


class MachineRefKind(StrEnum):
    ME = "me"
    POD = "pod"
    MINE = "mine"
    ID = "id"


@dataclass(frozen=True, slots=True)
class MachineRef:
    """A parsed ref. Parsing never touches the database.

    ``pod:<pod>/<name>`` also accepts ``pod:<pod>:<name>``, because a slash
    cannot travel inside one URL path segment; the SDKs and the CLI send the
    colon form. ``pod:<name>`` means "this pod" and needs a pod in context.
    """

    kind: MachineRefKind
    name: str | None = None
    pod: str | None = None
    machine_id: UUID | None = None

    @classmethod
    def parse(cls, raw: str) -> MachineRef:
        text = raw.strip()
        if text in ("", "me", "default"):
            return cls(MachineRefKind.ME)
        if text.startswith("pod:"):
            rest = text[len("pod:") :]
            pod, sep, name = rest.replace("/", ":", 1).rpartition(":")
            if not sep:
                pod, name = "", rest
            return cls(
                MachineRefKind.POD, name=validate_machine_name(name), pod=pod or None
            )
        if text.startswith("mine:"):
            return cls(
                MachineRefKind.MINE, name=validate_machine_name(text[len("mine:") :])
            )
        try:
            return cls(MachineRefKind.ID, machine_id=UUID(text))
        except ValueError:
            raise ValueError(
                f"'{raw}' is not a machine ref; use me, pod:<pod>/<name>, "
                "mine:<name> or a machine id"
            ) from None

    def __str__(self) -> str:
        if self.kind is MachineRefKind.ME:
            return "me"
        if self.kind is MachineRefKind.POD:
            return f"pod:{self.pod}/{self.name}" if self.pod else f"pod:{self.name}"
        if self.kind is MachineRefKind.MINE:
            return f"mine:{self.name}"
        return str(self.machine_id)


class MachineStatus(StrEnum):
    """What a caller is told, collapsed from desired state and instance state."""

    RUNNING = "running"
    STARTING = "starting"
    STOPPED = "stopped"
    OFFLINE = "offline"
    ERROR = "error"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class Machine:
    """A machine as the resolver hands it to a controller or a tool."""

    id: UUID
    kind: MachineKind
    name: str
    display_name: str
    template: MachineTemplate
    size: MachineSize
    fabric: MachineFabric
    capabilities: Capabilities
    visibility: MachineVisibility
    billing_scope: MachineBillingScope
    #: The user for a default or connected machine, the pod for a pod machine.
    owner_id: UUID
    pod_id: UUID | None = None
    organization_id: UUID | None = None
    created_by: UUID | None = None
    connection_id: UUID | None = None
    run_slots: int = 1
    #: Where the machine's home is. A connected machine in host mode reports
    #: its own root; every other fabric is built with the sandbox home.
    home: str = HOME_ROOT
    status: MachineStatus = MachineStatus.STOPPED
    last_used_at: datetime | None = None
    #: Pod members granted a RESTRICTED machine; agents and functions are
    #: granted through the pod's resource grants instead.
    granted_user_ids: frozenset[UUID] = field(default_factory=frozenset)

    @property
    def ref(self) -> str:
        """How to name this machine, in a URL path as much as anywhere.

        `pod:<pod>:<name>`, not `pod:<pod>/<name>`. Both parse, but this is
        what the API hands out, and the slash form cannot sit in a path
        segment: encoded or not, `/machines/pod:<pod>/qa` reaches no route,
        so a client using the `ref` it was just given got a 404.
        """
        if self.kind is MachineKind.DEFAULT:
            return "me"
        if self.kind is MachineKind.POD:
            return f"pod:{self.pod_id}:{self.name}"
        return f"mine:{self.name}"


__all__ = [
    "Capabilities",
    "DEFAULT_MACHINE_SIZE",
    "KNOWN_SIZES",
    "LEGACY_DEFAULT_SIZE",
    "MACHINE_NAME",
    "Machine",
    "MachineBillingScope",
    "MachineFabric",
    "MachineKind",
    "MachineRef",
    "MachineRefKind",
    "MachineSize",
    "MachineStatus",
    "MachineTemplate",
    "MachineVisibility",
    "TEMPLATE_SIZES",
    "template_builds_on",
    "validate_machine_name",
]
