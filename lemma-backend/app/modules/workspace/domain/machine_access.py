"""Who may do what with a machine: the authorization matrix, as one function.

Pure on purpose. The facts it needs -- the caller's standing in the machine's
pod, the grant rows, an app's consent, whether a run was started in Lemma --
are gathered by ``infrastructure/machine_facts.py`` and handed in, so every
cell of the matrix is a table row in a unit test rather than a fixture.

The levels are cumulative: ``STATUS`` lets a caller see that a machine exists
and how it is; ``USE`` adds processes, files, previews and the browser;
``MANAGE`` adds resizing, resetting and deleting. ``NONE`` is reported to the
caller as "not found", never "forbidden", so a RESTRICTED machine's name does
not leak to the people it is kept from.
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
    #: The app's name, from Lemma's own records -- for the consent card, which
    #: must not take an app's word for what it is called.
    app_name: str | None = None
    #: The run's conversation, for an agent; how "started in Lemma" is decided.
    conversation_id: UUID | None = None


class PodStanding(IntEnum):
    """The caller's role in a pod, as far as machines care."""

    NONE = 0
    VIEWER = 1
    EDITOR = 2
    ADMIN = 3


@dataclass(frozen=True, slots=True)
class MachineFacts:
    """What the matrix needs to know beyond the caller and the machine."""

    #: The person's standing in the machine's pod (or the pod it is shared to).
    standing: PodStanding = PodStanding.NONE
    #: A `machine.use` grant to the calling agent or function.
    workload_granted: bool = False
    #: A `machine.use` grant to the person, for a RESTRICTED machine.
    member_granted: bool = False
    #: The viewer said yes to this app running code on their machine.
    app_consented: bool = False
    #: The agent run was started by its person, in Lemma itself -- not by a
    #: channel message, a schedule or another surface.
    run_started_in_lemma: bool = False


class DenialReason(StrEnum):
    NOT_FOUND = "not_found"
    CONSENT_REQUIRED = "consent_required"


@dataclass(frozen=True, slots=True)
class AccessDecision:
    access: MachineAccess
    #: Why it is NONE, when it is: an app that has not asked yet is told to
    #: ask, everything else is told the machine does not exist.
    reason: DenialReason | None = None

    @classmethod
    def deny(cls, reason: DenialReason = DenialReason.NOT_FOUND) -> AccessDecision:
        return cls(MachineAccess.NONE, reason)


def decide(
    caller: MachineCaller, machine: Machine, facts: MachineFacts
) -> AccessDecision:
    if machine.kind is MachineKind.DEFAULT:
        return _default_machine(caller, machine, facts)
    if machine.kind is MachineKind.POD:
        return _pod_machine(caller, machine, facts)
    return _connected_machine(caller, machine, facts)


def _default_machine(
    caller: MachineCaller, machine: Machine, facts: MachineFacts
) -> AccessDecision:
    """A person's own machine: theirs, their in-Lemma runs', and their apps'
    once they said yes. Never a channel's, a schedule's or a function's."""
    if caller.user_id != machine.owner_id:
        return AccessDecision.deny()
    if caller.kind is CallerKind.USER:
        return AccessDecision(MachineAccess.MANAGE)
    if caller.kind is CallerKind.APP:
        if not facts.app_consented:
            return AccessDecision.deny(DenialReason.CONSENT_REQUIRED)
        return AccessDecision(MachineAccess.USE)
    if caller.kind is CallerKind.AGENT and facts.run_started_in_lemma:
        return AccessDecision(MachineAccess.USE)
    return AccessDecision.deny()


def _pod_machine(
    caller: MachineCaller, machine: Machine, facts: MachineFacts
) -> AccessDecision:
    if caller.kind in (CallerKind.AGENT, CallerKind.FUNCTION):
        return _workload_on_pod_machine(caller, machine, facts)
    if caller.kind is CallerKind.APP and caller.pod_id != machine.pod_id:
        # An app's page acts for its viewer only in its own pod.
        return AccessDecision.deny()
    # A person, or an app acting as its viewer in its own pod -- the same rule.
    restricted = machine.visibility is MachineVisibility.RESTRICTED
    if facts.standing is PodStanding.ADMIN:
        return AccessDecision(MachineAccess.MANAGE)
    if facts.standing is PodStanding.EDITOR:
        if restricted and not facts.member_granted:
            return AccessDecision.deny()
        return AccessDecision(MachineAccess.MANAGE)
    if facts.standing is PodStanding.VIEWER:
        # A viewer only ever sees how a machine is. A grant names a
        # RESTRICTED machine to them; it does not make them its user.
        if restricted and not facts.member_granted:
            return AccessDecision.deny()
        return AccessDecision(MachineAccess.STATUS)
    return AccessDecision.deny()


def _workload_on_pod_machine(
    caller: MachineCaller, machine: Machine, facts: MachineFacts
) -> AccessDecision:
    """An agent of the pod may use a POD machine; a RESTRICTED one, or any
    machine for a function, takes a grant. Another pod's workload never."""
    if caller.pod_id != machine.pod_id:
        return AccessDecision.deny()
    if facts.workload_granted:
        return AccessDecision(MachineAccess.USE)
    if caller.kind is CallerKind.AGENT and machine.visibility is MachineVisibility.POD:
        return AccessDecision(MachineAccess.USE)
    return AccessDecision.deny()


def _connected_machine(
    caller: MachineCaller, machine: Machine, facts: MachineFacts
) -> AccessDecision:
    """A laptop or VM: its owner's; a pod's only where the owner shared it,
    and then to agents and functions only with a grant. Never an app's -- a
    page running code on somebody's own computer is not something one
    consent card should be able to buy."""
    if caller.kind is CallerKind.APP:
        return AccessDecision.deny()
    if caller.kind is CallerKind.USER and caller.user_id == machine.owner_id:
        return AccessDecision(MachineAccess.MANAGE)
    shared = machine.pod_id is not None and machine.visibility is not (
        MachineVisibility.PRIVATE
    )
    if not shared:
        return AccessDecision.deny()
    if caller.kind in (CallerKind.AGENT, CallerKind.FUNCTION):
        if caller.pod_id == machine.pod_id and facts.workload_granted:
            return AccessDecision(MachineAccess.USE)
        return AccessDecision.deny()
    if facts.standing >= PodStanding.EDITOR:
        return AccessDecision(MachineAccess.USE)
    if facts.standing is PodStanding.VIEWER:
        return AccessDecision(MachineAccess.STATUS)
    return AccessDecision.deny()


__all__ = [
    "AccessDecision",
    "CallerKind",
    "DenialReason",
    "MachineAccess",
    "MachineCaller",
    "MachineFacts",
    "PodStanding",
    "decide",
]
