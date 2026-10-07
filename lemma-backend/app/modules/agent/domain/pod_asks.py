"""One pod asking another: a teammate hands work to a teammate and gets the answer.

An ask is not a record of its own. It *is* the conversation it opens in the pod
being asked (B), which carries under ``metadata["ask"]`` where it came from: the
asking pod (A), the conversation there that is owed the answer, the person it is
for, and the chain of pods it has passed through. When a run in B finishes and
leaves the conversation settled rather than waiting on someone, that run's last
words are the answer, and they are delivered into A's conversation the way
replies to ``message_user`` are: as input that starts A's next turn.

**Who the answer reaches decides whose authority B works with.** A conversation
belongs to one person and nobody else can read it, so an ask made in a person's
own conversation, by the assistant acting as them, can have B answer *as that
person*: it discloses nothing they could not have read by asking B themselves.
That is the only mode built so far (``AskMode.AS_PERSON``). A run nobody is
present for, a named agent, or a thread other people read -- a channel, a group,
a stranger's question -- has an audience the person's access was never granted
to, and is refused here.

**A chain stops at three hops and never revisits a pod.** Every ask carries the
pods it has already passed through, and an ask made from a conversation that is
itself answering one continues that chain. A→B→A is refused rather than allowed
to ping-pong; B needing something from A says so in its answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from app.core.domain.errors import DomainError
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import (
    ConversationType,
    JsonObject,
    MessageKind,
    MessageRole,
)

#: The metadata key on B's conversation, and the value of ``source`` there and on
#: the message that carries the request.
ASK_KEY = "ask"
ASK_SOURCE = "pod_ask"
#: ``source`` on the message that delivers B's answer into A's conversation.
ANSWER_SOURCE = "pod_ask_answer"
#: Set on A's conversation once it has asked, so a run finishing there knows to
#: collect any answer that arrived while it could not take one.
ASKS_OUT_KEY = "pod_asks_out"

#: Hops after the first pod. A→B is one, A→B→C two, A→B→C→D three.
MAX_ASK_DEPTH = 3

#: Surfaces whose thread is read by one person. Anything else -- a channel, an
#: email thread that can carry a Cc -- has readers the asker's access was not
#: granted to.
_PRIVATE_SURFACE_KINDS = frozenset({"DM"})

#: The conversation key a schedule stamps when it opens a conversation.
_STARTED_BY_KEY = "started_by"
#: The conversation key a sub-agent's conversation carries.
_SUB_AGENT_KEY = "is_sub_agent"
#: ``source`` on a turn the platform started by handing something back -- an
#: answer from another pod, or replies to ``message_user`` (``REPLY_SOURCE`` in
#: ``services/message_reply_service.py``). Nobody typed it, so it is no
#: person's say-so to ask anyone anything.
HANDED_BACK_SOURCES = frozenset({ANSWER_SOURCE, "message_replies"})


class AskMode(str, Enum):
    """Whose authority the pod being asked works with."""

    #: As the person the ask is for, who is a member of both pods.
    AS_PERSON = "AS_PERSON"
    #: As the asking pod, over a link the asked pod's people made: Public reads
    #: there plus what they granted it (``core/authorization/pod_principal``).
    LINK = "LINK"


class AskRefused(DomainError):
    """An ask that may not be made, with the reason in words the agent can relay."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="pod_ask_refused", status_code=409)


@dataclass(frozen=True, slots=True)
class AskChain:
    """The pods an ask has passed through, in order, starting with the first asker."""

    chain_id: UUID
    depth: int
    pod_ids: tuple[UUID, ...]

    @classmethod
    def start(cls, from_pod_id: UUID) -> AskChain:
        return cls(chain_id=uuid4(), depth=0, pod_ids=(from_pod_id,))

    def onward_to(self, pod_id: UUID) -> AskChain:
        """This chain, one hop further, or a refusal saying why it can't go."""
        if pod_id in self.pod_ids:
            raise AskRefused(
                "That pod is already part of this request, further up the "
                "chain. Answer with what you have, and say what you still need."
            )
        if self.depth >= MAX_ASK_DEPTH:
            raise AskRefused(
                f"This request has already been passed on {self.depth} times, "
                "which is as far as a request may go. Answer with what you have."
            )
        return AskChain(
            chain_id=self.chain_id,
            depth=self.depth + 1,
            pod_ids=(*self.pod_ids, pod_id),
        )

    def to_metadata(self) -> JsonObject:
        return {
            "chain_id": str(self.chain_id),
            "depth": self.depth,
            "pod_ids": [str(pod_id) for pod_id in self.pod_ids],
        }

    @classmethod
    def from_metadata(cls, raw: object) -> AskChain | None:
        if not isinstance(raw, Mapping):
            return None
        try:
            pod_ids = tuple(UUID(str(value)) for value in raw.get("pod_ids") or ())
            return cls(
                chain_id=UUID(str(raw["chain_id"])),
                depth=int(raw["depth"]),
                pod_ids=pod_ids,
            )
        except KeyError, TypeError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class PodAsk:
    """What B's conversation records about the ask that opened it."""

    from_pod_id: UUID
    from_pod_name: str
    from_conversation_id: UUID
    #: The person behind the ask. Always set when asking as them; over a link,
    #: set when a person started the asking turn and None when nobody did.
    for_user_id: UUID | None
    mode: AskMode
    chain: AskChain
    #: The run in B whose answer has been delivered, so the same answer is
    #: never delivered twice -- once inline to a quick asker and again by the
    #: completion event, say.
    delivered_run_id: UUID | None = None
    #: Until when the asker is waiting for the answer inside its own tool call.
    #: The completion event leaves the answer to it until then, so a quick one
    #: comes back as the tool's result rather than as a message pushed into the
    #: turn that is still waiting for it.
    inline_until: datetime | None = None

    def awaited_inline(self, now: datetime) -> bool:
        return self.inline_until is not None and now < self.inline_until

    def to_metadata(self) -> JsonObject:
        return {
            "from_pod_id": str(self.from_pod_id),
            "from_pod_name": self.from_pod_name,
            "from_conversation_id": str(self.from_conversation_id),
            "for_user_id": str(self.for_user_id) if self.for_user_id else None,
            "mode": self.mode.value,
            "chain": self.chain.to_metadata(),
            "delivered_run_id": (
                str(self.delivered_run_id) if self.delivered_run_id else None
            ),
            "inline_until": self.inline_until.isoformat()
            if self.inline_until
            else None,
        }

    @classmethod
    def from_metadata(cls, raw: object) -> PodAsk | None:
        """The ask, or ``None`` for anything that does not read as one.

        Tolerant on purpose: this is read on every completed run in every pod,
        and a malformed value must cost that conversation its ask, never the
        event handler its run.
        """
        if not isinstance(raw, Mapping):
            return None
        chain = AskChain.from_metadata(raw.get("chain"))
        if chain is None:
            return None
        try:
            delivered = raw.get("delivered_run_id")
            person = raw.get("for_user_id")
            inline = raw.get("inline_until")
            ask = cls(
                from_pod_id=UUID(str(raw["from_pod_id"])),
                from_pod_name=str(raw.get("from_pod_name") or "Another pod"),
                from_conversation_id=UUID(str(raw["from_conversation_id"])),
                for_user_id=UUID(str(person)) if person else None,
                mode=AskMode(str(raw["mode"])),
                chain=chain,
                delivered_run_id=UUID(str(delivered)) if delivered else None,
                inline_until=datetime.fromisoformat(str(inline)) if inline else None,
            )
        except KeyError, TypeError, ValueError:
            return None
        # Asking as a person with no person is not an ask anybody could make.
        if ask.mode is AskMode.AS_PERSON and ask.for_user_id is None:
            return None
        return ask


def ask_of(conversation: Conversation | None) -> PodAsk | None:
    """The ask this conversation is answering, if it was opened by one."""
    if conversation is None or not isinstance(conversation.metadata, dict):
        return None
    return PodAsk.from_metadata(conversation.metadata.get(ASK_KEY))


def linked_asker(conversation: Conversation | None) -> UUID | None:
    """The pod this conversation is answering over a link, if it is."""
    ask = ask_of(conversation)
    return ask.from_pod_id if ask is not None and ask.mode is AskMode.LINK else None


def with_ask_kept(
    existing: dict[str, object] | None, incoming: dict[str, object] | None
) -> dict[str, object] | None:
    """``incoming`` metadata, with the ask kept exactly as the server wrote it.

    The ask names the conversation an answer is delivered into, and delivery
    starts a turn there. A client that could rewrite it could aim B's answer --
    and a turn -- at a conversation of its choosing, so it is the server's to
    write and nobody's to change or drop.
    """
    previous = (existing or {}).get(ASK_KEY)
    if incoming is None and previous is None:
        return None
    kept = dict(incoming or {})
    kept.pop(ASK_KEY, None)
    if previous is not None:
        kept[ASK_KEY] = previous
    return kept


def without_ask(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Client-supplied metadata, minus any claim to be answering an ask."""
    if not metadata or ASK_KEY not in metadata:
        return metadata
    return {key: value for key, value in metadata.items() if key != ASK_KEY}


@dataclass(frozen=True, slots=True)
class AskingRun:
    """What about the run making an ask decides whether it may ask as its person."""

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
    _refuse_other_readers(run)
    _refuse_without_the_person(run)
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

    The link's grants were made for everyone in the asking pod, so who reads the
    answer matters less than as a person; what matters is that the run asking is
    this pod's own assistant, acting for the pod and not for somebody outside it,
    and that it is not handing work back and forth with nobody to say so.
    """
    if to_pod_id == run.pod_id:
        raise AskRefused("That is this pod. Do the work here instead.")
    if not run.is_pod_default_agent:
        raise AskRefused(
            "Only the pod's own assistant can ask over a link. A named agent "
            "can't ask other pods yet."
        )
    if run.answers_outsider:
        raise AskRefused(
            "This conversation answers someone outside the pod, and a linked "
            "pod's answer would reach them. Answer with what you have here."
        )
    if run.latest_turn_source in HANDED_BACK_SOURCES:
        raise AskRefused(
            "This turn started with an answer coming back. Pass on what came "
            "back; asking again waits for something new to ask about."
        )
    metadata = (
        run.conversation.metadata if isinstance(run.conversation.metadata, dict) else {}
    )
    if metadata.get(_SUB_AGENT_KEY) is True:
        raise AskRefused(
            "A sub-agent can't ask other pods. Return what you found, and let "
            "the conversation that started you ask."
        )
    if ask_of(run.conversation) is not None:
        raise AskRefused(
            "This conversation is answering a request from another pod. Answer "
            "with what you have."
        )
    return AskChain.start(run.pod_id).onward_to(to_pod_id)


def plan_ask(
    run: AskingRun, *, to_pod_id: UUID, through_you: bool, connected: bool
) -> tuple[AskMode, AskChain]:
    """How this run would ask that pod: as its person if it can, else over a link.

    As the person first: their access is the wider one, and they are here to
    approve it. A refusal there -- nobody started this turn, other people read
    the thread -- falls back to the link when there is one, because the link was
    approved for exactly the asks a person can't stand behind.
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


def _refuse_other_readers(run: AskingRun) -> None:
    """Refuse when anyone but the person would read the answer, or act on it."""
    if not run.is_pod_default_agent:
        raise AskRefused(
            "Only the pod's own assistant can ask another pod, acting as the "
            "person it works for. A named agent can't ask other pods yet."
        )
    if run.answers_outsider:
        raise AskRefused(
            "This conversation answers people outside the pod, and another "
            "pod's answer would reach them. Pass the question to a member with "
            "message_user instead."
        )
    kind = (run.surface_conversation_kind or "").upper() or None
    if kind is not None and kind not in _PRIVATE_SURFACE_KINDS:
        raise AskRefused(
            "Other people read this thread, and the answer would come back "
            "here with the access of the person asking. Ask from a direct "
            "message or from Lemma instead."
        )


def _refuse_without_the_person(run: AskingRun) -> None:
    """Refuse when the person did not start this conversation themselves."""
    conversation = run.conversation
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    if conversation.user_id != run.user_id:
        raise AskRefused("Only the person this conversation belongs to can ask.")
    if conversation.type is ConversationType.TASK or (
        str(metadata.get(_STARTED_BY_KEY) or "").upper() == "SCHEDULE"
    ):
        raise AskRefused(
            "Nobody started this conversation by hand, so there is no person "
            "here to ask as. Asking on a schedule needs a link between the "
            "pods, which isn't available yet."
        )
    if metadata.get(_SUB_AGENT_KEY) is True:
        raise AskRefused(
            "A sub-agent can't ask other pods. Return what you found, and let "
            "the conversation that started you ask."
        )
    # An answer arriving starts a turn, and that turn is the answer's, not the
    # person's. Asking again from it -- the same pod, or the next one -- would
    # let two pods hand work back and forth with nobody saying so, each round a
    # fresh chain the depth limit never sees.
    if run.latest_turn_source in HANDED_BACK_SOURCES:
        raise AskRefused(
            "This turn started with an answer coming back, not with something "
            "the person wrote. Tell them what came back; if more is needed, "
            "they can ask for it."
        )


def answer_from(messages: Sequence[Message], *, run_id: UUID) -> str | None:
    """What a run said last, after its last tool call: the answer to the ask.

    Text written before a tool call is narration ("let me check the ledger"), so
    only what follows the last call counts. A run that called nothing has no
    narration to skip. A run may answer across several messages, so they are
    joined rather than only the last one kept.
    """
    own = [message for message in messages if message.agent_run_id == run_id]
    last_tool = -1
    for index, message in enumerate(own):
        if message.kind in (MessageKind.TOOL_CALL, MessageKind.TOOL_RETURN):
            last_tool = index
    texts = [
        message.text.strip()
        for message in own[last_tool + 1 :]
        if message.role is MessageRole.ASSISTANT
        and message.kind is MessageKind.TEXT
        and message.text
        and message.text.strip()
    ]
    return "\n\n".join(texts) or None


def answer_message(*, pod_name: str, answer: str | None) -> str:
    """The turn delivered into A's conversation once B has answered."""
    if answer:
        return f"{pod_name} answered your request:\n\n{answer}"
    return (
        f"{pod_name} finished your request without writing an answer. Open "
        "its conversation to see what it did, or ask again more specifically."
    )


def unfinished_message(*, pod_name: str, reason: str | None) -> str:
    """The turn delivered into A's conversation when B's run did not finish."""
    said = f" ({reason})" if reason else ""
    return (
        f"{pod_name} could not finish your request{said}. Tell the person what "
        "happened, and ask again only if it is worth retrying."
    )


def instructions_for_answering(*, from_pod_name: str, person: str) -> str:
    """What B's conversation tells its assistant about the request it holds."""
    return (
        f"This conversation was opened by {from_pod_name}, another pod in your "
        f"organization, asking on behalf of {person}. {person} is a member here, "
        "so you are working as them, with their access.\n\n"
        f"Your final message goes back to {from_pod_name} as your answer. Make "
        "it complete and self-contained: nobody reads the rest of this "
        f"conversation unless they open it. {person} is not watching here, so "
        "don't ask with `ask_user`. If you need something you don't have, say "
        "exactly what in your answer."
    )


def ask_message_metadata(
    *,
    from_pod_id: UUID,
    from_pod_name: str,
    from_conversation_id: UUID,
    mode: AskMode,
) -> JsonObject:
    """``metadata`` on the message in B that carries the request.

    Named and moded so the page draws it as the asking pod's, not as the
    conversation owner's own words: asked as the person it is theirs at one
    remove; over a link nobody in B typed it at all.
    """
    return {
        "source": ASK_SOURCE,
        "ask_from_pod_id": str(from_pod_id),
        "ask_from_pod_name": from_pod_name,
        "ask_from_conversation_id": str(from_conversation_id),
        "ask_mode": mode.value,
    }


def answer_message_metadata(
    *, to_pod_id: UUID, to_pod_name: str, ask_conversation_id: UUID, run_id: UUID
) -> JsonObject:
    """``metadata`` on the message in A that delivers the answer."""
    return {
        "source": ANSWER_SOURCE,
        "asked_pod_id": str(to_pod_id),
        "asked_pod_name": to_pod_name,
        "ask_conversation_id": str(ask_conversation_id),
        "ask_run_id": str(run_id),
    }
