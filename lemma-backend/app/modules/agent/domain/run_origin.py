"""What started a conversation's turns, and who is waiting on them -- from the row.

Read twice for every turn: when it starts, to pick the work class it runs in
(nobody watches a schedule's turn live, so it never queues ahead of a person's
chat), and when it ends, to fill in the ``AgentRunFinished`` fact whose
subscribers decide with a pure predicate whether the run is theirs. Both read
the same stored keys, so a turn cannot start as one kind of run and finish as
another.

Pure: a view over the conversation's metadata and parent link, nothing read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.core.origin import Origin, OriginKind
from app.modules.agent.domain.conversation_metadata import ConversationMetadataFields
from app.modules.agent.domain.pod_asks import ASK_KEY, ASKS_OUT_KEY
from app.modules.agent.domain.subagent_results import (
    SUBAGENTS_OUT_KEY,
    reports_to_parent,
)

#: The same vocabulary as ``contracts.run_facts`` (which validates it); spelled
#: here so the domain does not import its module's contracts.
RunOriginName = Literal[
    "chat", "surface", "schedule", "workflow", "trigger", "subagent", "pod_ask"
]
ParentKind = Literal["workflow", "agent", "pod_ask"]

#: ``source`` a schedule stamps on the conversation it opens.
SCHEDULE_SOURCE = "SCHEDULE"
#: Keys a surface binding stamps on a conversation it answers on.
_SURFACE_KEYS = ("surface_platform", "surface_id")


@dataclass(frozen=True, slots=True)
class RunOrigin:
    """Where a conversation's turns come from, and who hears when one ends."""

    origin: RunOriginName
    parent_kind: ParentKind | None = None
    parent_ref: str | None = None
    has_reply_target: bool = False
    #: Sub-agent results this conversation is owed or owes (either side).
    settles_subagents: bool = False
    #: Pod-to-pod answers this conversation is owed or owes (either side).
    settles_pod_asks: bool = False


def run_origin(
    *,
    metadata: Mapping[str, object] | None,
    parent_id: object | None,
    conversation_id: object,
) -> RunOrigin:
    """The origin of a conversation with ``metadata`` and ``parent_id``.

    Precedence follows who is *waiting*: a workflow step and a pod that asked
    both wait on the outcome, a sub-agent's parent may; a schedule only records
    it, a surface only gets the reply.
    """
    stored: Mapping[str, object] = metadata or {}
    fields = ConversationMetadataFields.of(stored)
    has_reply_target = any(stored.get(key) for key in _SURFACE_KEYS)
    settles_subagents = bool(
        (parent_id is not None and reports_to_parent(stored))
        or stored.get(SUBAGENTS_OUT_KEY) is True
    )
    settles_pod_asks = bool(stored.get(ASK_KEY) or stored.get(ASKS_OUT_KEY))
    common = {
        "has_reply_target": has_reply_target,
        "settles_subagents": settles_subagents,
        "settles_pod_asks": settles_pod_asks,
    }
    if fields.workflow_run_id is not None:
        # A workflow's AGENT step waits on the conversation itself: its wait's
        # external ref is the conversation id.
        return RunOrigin(
            origin="workflow",
            parent_kind="workflow",
            parent_ref=str(conversation_id),
            **common,
        )
    if stored.get(ASK_KEY):
        return RunOrigin(
            origin="pod_ask", parent_kind="pod_ask", parent_ref=None, **common
        )
    if parent_id is not None:
        return RunOrigin(
            origin="subagent", parent_kind="agent", parent_ref=str(parent_id), **common
        )
    if (fields.source or "").upper() == SCHEDULE_SOURCE:
        return RunOrigin(origin="schedule", **common)
    if has_reply_target:
        return RunOrigin(origin="surface", **common)
    return RunOrigin(origin="chat", **common)


#: What fired a schedule, as the platform's origin. A schedule stamps its type
#: on the conversation it opens (``schedule.services.target_start``).
_SCHEDULE_TYPE_ORIGINS: dict[str, OriginKind] = {
    "TIME": OriginKind.SCHEDULE,
    "DATASTORE": OriginKind.DATA_TRIGGER,
    "WEBHOOK": OriginKind.CONNECTOR,
}


def conversation_origin(
    *,
    metadata: Mapping[str, object] | None,
    parent_id: object | None,
    conversation_id: object,
) -> Origin | None:
    """How a turn's work arrived (``app.core.origin``), as far as its row can say.

    A turn runs as a work item, which starts with no origin bound: the request
    that queued it -- or the webhook behind the surface message that did -- is
    gone by then. The conversation still records what started it, and for
    automation and surfaces that is the answer. A person's chat, a sub-agent and
    a pod's ask say nothing about which client was used, so they answer
    ``None`` rather than a guess.
    """
    stored: Mapping[str, object] = metadata or {}
    kind = run_origin(
        metadata=stored, parent_id=parent_id, conversation_id=conversation_id
    ).origin
    if kind == "workflow":
        return Origin(OriginKind.WORKFLOW)
    if kind == "schedule":
        schedule_type = str(stored.get("schedule_type") or "").upper()
        return Origin(_SCHEDULE_TYPE_ORIGINS.get(schedule_type, OriginKind.SCHEDULE))
    if kind == "surface":
        platform = stored.get("surface_platform")
        # Stored uppercase (`SurfacePlatform`); the origin allowlist is lowercase.
        return Origin(
            OriginKind.SURFACE,
            platform=platform.lower() if isinstance(platform, str) else None,
        )
    return None


__all__ = ["RunOrigin", "SCHEDULE_SOURCE", "conversation_origin", "run_origin"]
