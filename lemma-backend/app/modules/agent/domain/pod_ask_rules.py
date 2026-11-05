"""Who may ask another pod, and how: the rules ``plan_ask`` applies.

Every rule is about the answer's audience, because the answer lands in the
asking conversation. As the person, it must be a thread only they read, started
by them. Over a link, it may be a thread nobody reads -- a schedule, a task --
but never one anybody else does. Either way: the pod's own assistant, no
stranger's question, no sub-agent, and no turn an answer coming back started.

``open_modes`` is the same rules without a pod to ask, for what the brief and
``list_teammates`` may offer (``pod_asks`` holds what an ask *is*).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import (
    HANDED_BACK_SOURCES,
    AskChain,
    AskMode,
    AskRefused,
    ask_of,
)
from app.modules.agent.domain.value_objects import ConversationType

#: Surfaces whose thread is read by one person. Anything else -- a channel, an
#: email thread that can carry a Cc -- has readers the asker's access was not
#: granted to.
_PRIVATE_SURFACE_KINDS = frozenset({"DM"})

#: The conversation key a schedule stamps when it opens a conversation.
_STARTED_BY_KEY = "started_by"
#: The conversation key a sub-agent's conversation carries.
_SUB_AGENT_KEY = "is_sub_agent"


@dataclass(frozen=True, slots=True)
class AskingRun:
    """What about the run making an ask decides whether, and how, it may ask."""

    user_id: UUID
    pod_id: UUID
    conversation: Conversation
    is_pod_default_agent: bool
    answers_outsider: bool
    surface_conversation_kind: str | None
    #: ``source`` on the newest message in the conversation from the user side:
    #: what started the turn that is asking.
    latest_turn_source: str | None = None


def chain_for_person(run: AskingRun, *, to_pod_id: UUID) -> AskChain:
    """The chain an ask from this run would carry, or a refusal saying why not.

    Every refusal is about the answer's audience. It lands in this conversation,
    so this conversation has to be one only the person reads, started by them,
    with the assistant acting as them.
    """
    if to_pod_id == run.pod_id:
        raise AskRefused("That is this pod. Do the work here instead.")
    _raise(_person_refusal(run))
    current = ask_of(run.conversation)
    if current is None:
        return AskChain.start(run.pod_id).onward_to(to_pod_id)
    # Answering an ask already: carry on as the same person, or not at all.
    if current.mode is not AskMode.AS_PERSON or current.for_user_id != run.user_id:
        raise AskRefused(
            "This conversation is answering a request from another pod, for "
            "someone else. Answer with what you have."
        )
    return current.chain.onward_to(to_pod_id)


def chain_for_link(run: AskingRun, *, to_pod_id: UUID) -> AskChain:
    """The chain an ask over a link would carry, or a refusal saying why not.

    The link's grants were made for the asking pod: its people and its own
    work. So the answer may land where nobody is present -- a schedule, a task
    -- but never in a thread read by anyone else: a channel, an email thread
    that can carry a Cc, a stranger's question. And it is never a step in a
    longer chain: a conversation answering an ask can't pass it on over a link.
    """
    if to_pod_id == run.pod_id:
        raise AskRefused("That is this pod. Do the work here instead.")
    _raise(_link_refusal(run))
    return AskChain.start(run.pod_id).onward_to(to_pod_id)


def plan_ask(
    run: AskingRun, *, to_pod_id: UUID, through_you: bool, connected: bool
) -> tuple[AskMode, AskChain]:
    """How this run would ask that pod: as its person if it can, else over a link.

    As the person first: their access is the wider one, and they are here to
    approve it. A refusal there because nobody is present -- a schedule, a task
    -- falls back to the link when there is one, because the link was approved
    for exactly the asks a person can't stand behind. A refusal because other
    people read the thread does not: the link refuses those too.
    """
    if through_you:
        try:
            return AskMode.AS_PERSON, chain_for_person(run, to_pod_id=to_pod_id)
        except AskRefused:
            if not connected:
                raise
    if connected:
        return AskMode.LINK, chain_for_link(run, to_pod_id=to_pod_id)
    raise AskRefused(
        "That pod isn't connected to this one, and the person isn't in it, so "
        "there is no way to ask it from here."
    )


@dataclass(frozen=True, slots=True)
class OpenModes:
    """Which ways of asking this run has at all, whatever pod it would ask."""

    as_person: bool
    over_link: bool


def open_modes(run: AskingRun) -> OpenModes:
    """What the brief and ``list_teammates`` may offer this run.

    The same checks ``plan_ask`` makes, without a pod to ask: so a pod is only
    named as askable here, and only by a way, that the ask itself would allow.
    """
    return OpenModes(
        as_person=_person_refusal(run) is None,
        over_link=_link_refusal(run) is None,
    )


def closed_reason(run: AskingRun) -> str | None:
    """Why this run can ask no pod at all, by either way -- so an empty list of
    pods says that, not "there is no other pod", which sends the agent and the
    person looking for a pod that would not help."""
    refusal = _audience_refusal(run) or _handed_back_refusal(run)
    return refusal.message if refusal is not None else None


def _raise(refusal: AskRefused | None) -> None:
    if refusal is not None:
        raise refusal


def _audience_refusal(run: AskingRun) -> AskRefused | None:
    """Why the answer would reach someone it may not, for either way of asking.

    It lands in this conversation, so: the pod's own assistant, not answering a
    stranger, in a thread one person reads -- or nobody does -- and not a
    sub-agent, whose parent is the one to ask.
    """
    if not run.is_pod_default_agent:
        return AskRefused(
            "Only the pod's own assistant can ask another pod. A named agent "
            "can't ask other pods yet."
        )
    if run.answers_outsider:
        return AskRefused(
            "This conversation answers people outside the pod, and another "
            "pod's answer would reach them. Call ask_a_person to bring a member "
            "in instead."
        )
    kind = (run.surface_conversation_kind or "").upper() or None
    if kind is not None and kind not in _PRIVATE_SURFACE_KINDS:
        return AskRefused(
            "Other people read this thread, and another pod's answer would come "
            "back here where they can read it. Ask from a direct message or "
            "from Lemma instead."
        )
    metadata = _metadata(run.conversation)
    if metadata.get(_SUB_AGENT_KEY) is True:
        return AskRefused(
            "A sub-agent can't ask other pods. Return what you found, and let "
            "the conversation that started you ask."
        )
    return None


def _handed_back_refusal(run: AskingRun) -> AskRefused | None:
    """Refuse asking from a turn the platform started by handing something back.

    An answer arriving starts a turn, and that turn is the answer's, not the
    person's. Asking again from it -- the same pod, or the next one -- would let
    two pods hand work back and forth with nobody saying so, each round a fresh
    chain the depth limit never sees.
    """
    if run.latest_turn_source in HANDED_BACK_SOURCES:
        return AskRefused(
            "This turn started with an answer coming back, not with something "
            "anyone asked for. Pass on what came back; if more is needed, ask "
            "for it on the next request."
        )
    return None


def _person_refusal(run: AskingRun) -> AskRefused | None:
    """Why this run can't ask as its person, or ``None`` when it can."""
    refusal = _audience_refusal(run) or _handed_back_refusal(run)
    if refusal is not None:
        return refusal
    conversation = run.conversation
    metadata = _metadata(conversation)
    if conversation.user_id != run.user_id:
        return AskRefused("Only the person this conversation belongs to can ask.")
    if conversation.type is ConversationType.TASK or (
        str(metadata.get(_STARTED_BY_KEY) or "").upper() == "SCHEDULE"
    ):
        return AskRefused(
            "Nobody started this conversation by hand, so there is no person "
            "here to ask as. Only a pod connected to this one can be asked from "
            "here."
        )
    return None


def _link_refusal(run: AskingRun) -> AskRefused | None:
    """Why this run can't ask over a link, or ``None`` when it can."""
    refusal = _audience_refusal(run) or _handed_back_refusal(run)
    if refusal is not None:
        return refusal
    if ask_of(run.conversation) is not None:
        return AskRefused(
            "This conversation is answering a request from another pod. Answer "
            "with what you have."
        )
    return None


def _metadata(conversation: Conversation) -> Mapping[str, object]:
    metadata = conversation.metadata
    return metadata if isinstance(metadata, dict) else {}
