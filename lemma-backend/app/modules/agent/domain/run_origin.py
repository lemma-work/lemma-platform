"""What started a conversation's turns, and who is waiting on them -- from the row.

Read twice for every turn: when it starts, to pick the work class it runs in
(nobody watches a schedule's turn live, so it never queues ahead of a person's
chat), and when it ends, to fill in the ``AgentRunFinished`` fact whose
subscribers decide with a pure predicate whether the run is theirs. Both read
the same stored columns, so a turn cannot start as one kind of run and finish
as another.

Pure: a view over the conversation's typed state, nothing read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.origin import Origin, OriginKind
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationOrigin

#: The same vocabulary as ``contracts.run_facts`` (which validates it); spelled
#: here so the domain does not import its module's contracts.
RunOriginName = Literal[
    "chat", "surface", "schedule", "workflow", "trigger", "subagent", "pod_ask"
]
ParentKind = Literal["workflow", "agent", "pod_ask"]

#: What a chat surface opens: its own threads, a contact's, a notification's.
_SURFACE_ORIGINS = frozenset(
    {
        ConversationOrigin.SURFACE,
        ConversationOrigin.CONTACT,
        ConversationOrigin.NOTIFICATION,
    }
)


@dataclass(frozen=True, slots=True)
class RunOrigin:
    """Where a conversation's turns come from, and who hears when one ends."""

    origin: RunOriginName
    parent_kind: ParentKind | None = None
    parent_ref: str | None = None
    #: Sub-agent results this conversation is owed or owes (either side).
    settles_subagents: bool = False
    #: Pod-to-pod answers this conversation is owed or owes (either side).
    settles_pod_asks: bool = False


def run_origin(conversation: Conversation) -> RunOrigin:
    """The origin of ``conversation``'s turns.

    Precedence follows who is *waiting*: a workflow step and a pod that asked
    both wait on the outcome, a sub-agent's parent may; a schedule only records
    it, a surface only gets the reply.
    """
    owes_parent = conversation.parent_id is not None and conversation.reports_to_parent
    common = {
        "settles_subagents": owes_parent or conversation.delegations_out,
        "settles_pod_asks": conversation.ask is not None
        or conversation.delegations_out,
    }
    origin = conversation.origin
    if origin is ConversationOrigin.WORKFLOW_STEP:
        # A workflow's AGENT step waits on the conversation itself: its wait's
        # external ref is the conversation id.
        return RunOrigin(
            origin="workflow",
            parent_kind="workflow",
            parent_ref=str(conversation.id),
            **common,
        )
    if origin is ConversationOrigin.POD_ASK:
        return RunOrigin(
            origin="pod_ask", parent_kind="pod_ask", parent_ref=None, **common
        )
    if origin is ConversationOrigin.SUBAGENT and conversation.parent_id is not None:
        return RunOrigin(
            origin="subagent",
            parent_kind="agent",
            parent_ref=str(conversation.parent_id),
            **common,
        )
    if origin is ConversationOrigin.SCHEDULE:
        return RunOrigin(origin="schedule", **common)
    if origin in _SURFACE_ORIGINS:
        return RunOrigin(origin="surface", **common)
    return RunOrigin(origin="chat", **common)


#: What fired a schedule, as the platform's origin (``Conversation.schedule_type``).
_SCHEDULE_TYPE_ORIGINS: dict[str, OriginKind] = {
    "TIME": OriginKind.SCHEDULE,
    "DATASTORE": OriginKind.DATA_TRIGGER,
    "WEBHOOK": OriginKind.CONNECTOR,
}


def conversation_origin(
    conversation: Conversation, *, platform: str | None = None
) -> Origin | None:
    """How a turn's work arrived (``app.core.origin``), as far as its row can say.

    A turn runs as a work item, which starts with no origin bound: the request
    that queued it -- or the webhook behind the surface message that did -- is
    gone by then. The conversation still records what started it, and for
    automation and surfaces that is the answer. A person's chat, a sub-agent and
    a pod's ask say nothing about which client was used, so they answer
    ``None`` rather than a guess. ``platform`` is the one its surface records.
    """
    kind = run_origin(conversation).origin
    if kind == "workflow":
        return Origin(OriginKind.WORKFLOW)
    if kind == "schedule":
        schedule_type = (conversation.schedule_type or "").upper()
        return Origin(_SCHEDULE_TYPE_ORIGINS.get(schedule_type, OriginKind.SCHEDULE))
    if kind == "surface" or platform:
        # Stored uppercase (`SurfacePlatform`); the origin allowlist is lowercase.
        return Origin(
            OriginKind.SURFACE, platform=platform.lower() if platform else None
        )
    return None


__all__ = ["RunOrigin", "conversation_origin", "run_origin"]
