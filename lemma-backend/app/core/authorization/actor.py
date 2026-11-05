"""Who is acting: the actor a ``Context`` carries, and the label it reports.

Split from ``context`` so the actor kinds read as one thing. ``Context.actor``
is one of these; its type *is* the actor kind, with no flag beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.core.authorization.principals import PrincipalRef


class ActorType(str, Enum):
    """The label an actor is reported under -- in audit rows, analytics and
    traces. Decisions read ``Context.actor``, whose type *is* the actor; this
    enum only names it. ``SYSTEM`` labels work no actor started (analytics)."""

    USER = "USER"
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"
    DELEGATED_USER_WORKLOAD = "DELEGATED_USER_WORKLOAD"
    SYSTEM = "SYSTEM"
    ANONYMOUS = "ANONYMOUS"
    #: Somebody outside the pod whom it knows by a vouched-for handle. Reads
    #: what an anonymous visitor reads, and the pod database narrows contact-
    #: owned tables to their own rows (``Context.contact_id``).
    CONTACT = "CONTACT"
    #: Another pod, asking this one over a link (``authorization.pod_principal``):
    #: Public reads here, plus whatever this pod granted that pod, and nothing
    #: in any other pod.
    POD = "POD"


@dataclass(frozen=True, slots=True)
class Delegation:
    """What a person's software carries about the run it is part of."""

    #: Session approvals (APPROVE_FOR_SESSION) are keyed by it.
    session_id: str | None = None
    #: Permissions the token was narrowed to; empty means not narrowed.
    scope: frozenset[str] = frozenset()
    #: How the workload is named in denials and audit ("research-agent").
    name: str | None = None


@dataclass(frozen=True, slots=True)
class Person:
    """A signed-in person acting for themselves."""

    user_id: UUID


@dataclass(frozen=True, slots=True)
class Assistant:
    """A pod's default agent, acting as the person who invoked it.

    It holds no grants of its own: it mirrors that person's access inside its
    one pod, and is refused anything org-scoped.
    """

    user_id: UUID
    delegation: Delegation = Delegation()


class WorkloadKind(str, Enum):
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"

    @classmethod
    def of(cls, principal_type: str) -> "WorkloadKind":
        """``"agent"`` / ``"AGENT"`` -> AGENT; anything else is a function."""
        return cls.AGENT if principal_type.upper() == "AGENT" else cls.FUNCTION


@dataclass(frozen=True, slots=True)
class Workload:
    """A named agent or function.

    With ``for_user_id`` it acts for that person and gets their access
    intersected with its own grants (PS-ACCESS-020). Without one it is a
    person-less run -- a function serving a contact or an anonymous visitor --
    and has its grants alone.
    """

    kind: WorkloadKind
    workload_id: UUID
    for_user_id: UUID | None = None
    #: The workload's own principals: its id and its roles. Grants to these
    #: are the workload half of the intersection.
    principal_refs: frozenset[PrincipalRef] = frozenset()
    delegation: Delegation = Delegation()


class OutsiderKind(str, Enum):
    ANONYMOUS = "ANONYMOUS"
    CONTACT = "CONTACT"


@dataclass(frozen=True, slots=True)
class Outsider:
    """Somebody outside the pod: an anonymous visitor, or a contact it knows by
    a vouched-for handle. Reads what is Public in one pod, nothing else."""

    kind: OutsiderKind
    contact_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class OtherPod:
    """Another pod, asking this one over a link: Public reads plus what this
    pod granted that pod (``authorization.pod_principal``)."""

    pod_id: UUID


Actor = Person | Assistant | Workload | Outsider | OtherPod


def actor_type_of(actor: Actor) -> ActorType:
    """The label ``actor`` is reported under."""
    match actor:
        case Person():
            return ActorType.USER
        case Assistant():
            return ActorType.DELEGATED_USER_WORKLOAD
        case Workload(for_user_id=None, kind=WorkloadKind.AGENT):
            return ActorType.AGENT
        case Workload(for_user_id=None):
            return ActorType.FUNCTION
        case Workload():
            return ActorType.DELEGATED_USER_WORKLOAD
        case Outsider(kind=OutsiderKind.ANONYMOUS):
            return ActorType.ANONYMOUS
        case Outsider():
            return ActorType.CONTACT
        case OtherPod():
            return ActorType.POD
