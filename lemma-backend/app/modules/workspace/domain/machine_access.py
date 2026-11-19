"""Who may do what with a machine that is not a pod resource.

A **pod machine** is a pod resource and is decided by the core authorizer
like any other (``services/machine_resolver``): members by their pod role,
agents and functions by a ``machine.use`` grant intersected with the person
they act for, the pod's default agent as its person, an app's page as its
viewer in its own pod. Nothing about it is decided here except the two limits
that hold whatever the authorizer says: an app's page never manages a
machine, and acts only in its own pod.

What is left here is pure, so every cell is a table row in a unit test:

- **a person's default machine** (``me``) is theirs and every run they own
  that answers a member -- chat, schedule, workflow, a channel message, a
  call, a sub-agent. A run answering a contact or another pod never reaches
  it, and neither does a function, an app or anybody else;
- **a connected machine** is its owner's; shared into a pod, it is that pod's
  resource as far as the authorizer goes, but never more than usable by
  anybody but its owner, and never by an app.

The levels are cumulative: ``STATUS`` lets a caller see that a machine exists
and how it is; ``USE`` adds processes, files, previews and the browser;
``MANAGE`` adds resizing, resetting and deleting. ``NONE`` is reported to the
caller as "not found", never "forbidden", so a machine's name does not leak
to the people it is kept from.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from uuid import UUID

from app.modules.workspace.domain.machine import (
    Machine,
    MachineKind,
    MachineVisibility,
)


class MachineAccess(IntEnum):
    NONE = 0
    STATUS = 1
    USE = 2
    MANAGE = 3


class CallerKind(StrEnum):
    #: A person in Lemma, the CLI or the SDK with their own session.
    USER = "user"
    #: An agent run, in-process or through its delegated token.
    AGENT = "agent"
    #: A pod function.
    FUNCTION = "function"
    #: A pod app's page, calling with the viewer's own session.
    APP = "app"


@dataclass(frozen=True, slots=True)
class MachineCaller:
    kind: CallerKind
    #: The person: the user themself, the viewer of an app, the person a run
    #: acts for. None for a function with no invoking person.
    user_id: UUID | None
    #: The pod a workload belongs to, or the pod in the request's path.
    pod_id: UUID | None = None
    #: The agent or function id, for grant lookups.
    workload_id: UUID | None = None
    app_id: UUID | None = None
    #: The run's conversation, for an agent: whom it answers decides whether
    #: it may use its person's own machine.
    conversation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PodAccess:
    """What the core authorizer said about a machine as a pod resource."""

    manage: bool = False
    use: bool = False
    #: May read the pod the machine is in (``pod.read``).
    see: bool = False

    @property
    def level(self) -> MachineAccess:
        if self.manage:
            return MachineAccess.MANAGE
        if self.use:
            return MachineAccess.USE
        if self.see:
            return MachineAccess.STATUS
        return MachineAccess.NONE


def default_machine_access(
    caller: MachineCaller, machine: Machine, *, answers_member: bool
) -> MachineAccess:
    """A person's own machine: theirs to manage, and usable by every run they
    own that answers a member -- whatever started it.

    ``answers_member`` is whom the agent run's conversation answers. A run
    answering a contact or another pod is the pod's, acting toward somebody
    outside it; its toolset already has no machine, and this is the second
    line behind that.
    """
    if machine.kind is not MachineKind.DEFAULT:
        raise ValueError("default_machine_access is for a default machine")
    if caller.user_id is None or caller.user_id != machine.owner_id:
        return MachineAccess.NONE
    if caller.kind is CallerKind.USER:
        return MachineAccess.MANAGE
    if caller.kind is CallerKind.AGENT and answers_member:
        return MachineAccess.USE
    return MachineAccess.NONE


def pod_machine_access(
    caller: MachineCaller, machine: Machine, pod: PodAccess
) -> MachineAccess:
    """The authorizer's answer, with the two limits an app's page is held to.

    An app's page acts as its viewer only inside its own pod, and never
    manages a machine: creating, resizing, resetting and deleting are a
    person's own acts, not something an app's script does for them.
    """
    if caller.kind is CallerKind.APP:
        if caller.pod_id is None or caller.pod_id != machine.pod_id:
            return MachineAccess.NONE
        return min(pod.level, MachineAccess.USE)
    return pod.level


def connected_machine_access(
    caller: MachineCaller, machine: Machine, pod: PodAccess
) -> MachineAccess:
    """A laptop or VM: its owner's; shared into a pod, the pod's resource --
    but only ever usable, never managed, by anyone but its owner. Never an
    app's: a page running code on somebody's own computer is not something
    an app's script should be able to do."""
    if machine.kind is not MachineKind.CONNECTED:
        raise ValueError("connected_machine_access is for a connected machine")
    if caller.kind is CallerKind.APP:
        return MachineAccess.NONE
    if caller.kind is CallerKind.USER and caller.user_id == machine.owner_id:
        return MachineAccess.MANAGE
    if not is_shared(machine):
        return MachineAccess.NONE
    return min(pod.level, MachineAccess.USE)


def is_shared(machine: Machine) -> bool:
    """Whether a connected machine was shared into a pod by its owner."""
    return machine.pod_id is not None and machine.visibility is not (
        MachineVisibility.PRIVATE
    )


__all__ = [
    "CallerKind",
    "MachineAccess",
    "MachineCaller",
    "PodAccess",
    "connected_machine_access",
    "default_machine_access",
    "is_shared",
    "pod_machine_access",
]
