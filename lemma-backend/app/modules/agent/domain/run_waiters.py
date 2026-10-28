"""Who is waiting on a conversation's runs, and how a run's work arrived.

Who waits is the conversation's: a workflow's AGENT step and a pod that asked
both wait on its outcome, a sub-agent's parent may. It is read when a run
ends, to fill in the ``AgentRunFinished`` fact whose subscribers decide with a
pure predicate whether the run is theirs.

What *kind* of turn a run is, is the run's own (``run_kind``): stored when it
starts, never worked out again from the conversation.

Pure: a view over typed state, nothing read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.origin import Origin, OriginKind
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.domain.value_objects import ConversationOrigin

ParentKind = Literal["workflow", "agent", "pod_ask"]


@dataclass(frozen=True, slots=True)
class RunWaiters:
    """Who hears when one of a conversation's runs ends."""

    parent_kind: ParentKind | None = None
    parent_ref: str | None = None
    #: Sub-agent results this conversation is owed or owes (either side).
    settles_subagents: bool = False
    #: Pod-to-pod answers this conversation is owed or owes (either side).
    settles_pod_asks: bool = False


def run_waiters(conversation: Conversation) -> RunWaiters:
    """Who waits on ``conversation``'s runs.

    Precedence follows who is *waiting*: a workflow step and a pod that asked
    both wait on the outcome, a sub-agent's parent may.
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
        return RunWaiters(
            parent_kind="workflow", parent_ref=str(conversation.id), **common
        )
    if origin is ConversationOrigin.POD_ASK:
        return RunWaiters(parent_kind="pod_ask", **common)
    if origin is ConversationOrigin.SUBAGENT and conversation.parent_id is not None:
        return RunWaiters(
            parent_kind="agent", parent_ref=str(conversation.parent_id), **common
        )
    return RunWaiters(**common)


#: What fired a schedule, as the platform's origin (``Conversation.schedule_type``).
_SCHEDULE_TYPE_ORIGINS: dict[str, OriginKind] = {
    "TIME": OriginKind.SCHEDULE,
    "DATASTORE": OriginKind.DATA_TRIGGER,
    "WEBHOOK": OriginKind.CONNECTOR,
}

_PLATFORM_KINDS = frozenset({RunKind.SURFACE, RunKind.CONTACT})


def run_work_origin(
    kind: RunKind, conversation: Conversation, *, platform: str | None = None
) -> Origin | None:
    """How a run's work arrived (``app.core.origin``), as far as its kind says.

    A turn runs as a work item, which starts with no origin bound: the request
    that queued it -- or the webhook behind the surface message that did -- is
    gone by then. The run's kind still says what started it, and for
    automation and surfaces that is the answer. A person's chat, a sub-agent and
    a pod's ask say nothing about which client was used, so they answer
    ``None`` rather than a guess. ``platform`` is the one its surface records.
    """
    if kind is RunKind.WORKFLOW:
        return Origin(OriginKind.WORKFLOW)
    if kind is RunKind.SCHEDULE:
        schedule_type = (conversation.schedule_type or "").upper()
        return Origin(_SCHEDULE_TYPE_ORIGINS.get(schedule_type, OriginKind.SCHEDULE))
    if kind in _PLATFORM_KINDS or platform:
        # Stored uppercase (`SurfacePlatform`); the origin allowlist is lowercase.
        return Origin(
            OriginKind.SURFACE, platform=platform.lower() if platform else None
        )
    return None


__all__ = ["RunWaiters", "run_waiters", "run_work_origin"]
