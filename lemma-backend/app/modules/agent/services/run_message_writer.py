"""Persists harness-emitted messages and derives terminal run output.

Keeps the flat-message persistence rules (final-answer tagging, output
extraction, conversation-status derivation) in one place so the runner stays
focused on orchestration.

``split_reasoning_drafts`` is the newest of those rules and the one with the
widest blast radius, so it is stated here rather than in a harness: an assistant
message whose body is the model's *reasoning* is a THINKING message, never a
TEXT one. Some models write reasoning into the text channel as ``<think>`` tags
instead of returning it as a separate part, and everything downstream reads the
text channel as the answer -- it is shown as one, and
``output_data_from_event`` below hands it back as one to whatever called the
agent. Splitting here rather than in the pydantic-ai harness is what makes the
rule hold for every harness, including ones not written yet.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.text.thinking_tags import has_thinking_tokens, split_thinking_segments
from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.pausing_tools import (
    USER_PAUSING_TOOL_NAMES,
    WAIT_TOOL_NAME,
)
from app.modules.agent.domain.tool_call_ids import call_signature
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    ConversationStatus,
    JsonObject,
    JsonValue,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.infrastructure.repositories.run_last_words import (
    last_text,
    tool_result_after,
)

logger = get_logger(__name__)


def split_reasoning_drafts(draft: MessageDraft) -> list[MessageDraft]:
    """One assistant draft, split into the messages it should actually become.

    Returns the input unchanged in the overwhelming majority of cases -- this
    only fires when a model wrote its reasoning into the text channel. When it
    does fire the result is the thoughts first and the answer last, which is
    the order they happened in and the order a transcript has to show.

    Two shapes matter and neither is hypothetical:

    - reasoning *then* an answer: two drafts, and the answer keeps whatever the
      original carried (the final-answer flag, structured output).
    - reasoning and nothing else: **one** draft, the thought. There is no answer
      to record, and inventing an empty one would flag it as the final answer
      and publish it as the run's output.
    """
    if draft.role is not MessageRole.ASSISTANT or draft.kind is not MessageKind.TEXT:
        return [draft]
    if not has_thinking_tokens(draft.text):
        return [draft]

    metadata: JsonObject = dict(draft.metadata or {})
    drafts: list[MessageDraft] = []
    answer: str | None = None

    for kind, chunk in split_thinking_segments(draft.text or ""):
        if kind == "thinking":
            drafts.append(
                MessageDraft.of_thinking(
                    chunk.strip(),
                    role=draft.role,
                    metadata={
                        # Never the answer, whatever the original claimed, and
                        # marked so the row says why it is not a TEXT message.
                        **{
                            key: value
                            for key, value in metadata.items()
                            if key not in _ANSWER_ONLY_METADATA
                        },
                        "is_final_answer": False,
                        "reclassified_inline_reasoning": True,
                    },
                )
            )
        else:
            answer = f"{answer}{chunk}" if answer is not None else chunk

    if answer is not None and answer.strip():
        drafts.append(draft.model_copy(update={"text": answer.strip()}))

    # Debug, not warning: on a model that always writes reasoning inline this
    # fires every turn, and a warning per message is noise nobody reads. The
    # durable signal is `reclassified_inline_reasoning` on the row above --
    # queryable, permanent, and countable per model, which is what actually
    # answers "has a provider started doing this to us".
    logger.debug(
        "agent.run.inline_reasoning_reclassified.diagnostic",
        thought_count=sum(1 for one in drafts if one.kind is MessageKind.THINKING),
        answer_survived=any(one.kind is MessageKind.TEXT for one in drafts),
    )
    return drafts


#: Metadata that describes an *answer* and so must not ride along on a thought
#: split out of one. Left on the answer draft when there is one.
_ANSWER_ONLY_METADATA = frozenset(
    {
        "structured_output",
        "final_answer_status",
        "final_answer_error",
        "tool_call_id",
    }
)


def _restates(earlier: Message, draft: MessageDraft, agent_run_id: UUID) -> bool:
    """Whether ``earlier`` is this same call, stored before by this same run.

    A pause keeps its id within its run whatever its arguments: its card and
    decision row were written under that id as it was raised.
    """
    if earlier.agent_run_id != agent_run_id:
        return False
    if draft.tool_name in USER_PAUSING_TOOL_NAMES:
        return True
    return earlier.tool_name == draft.tool_name and call_signature(
        earlier.tool_name, earlier.tool_args
    ) == call_signature(draft.tool_name, draft.tool_args)


class RunMessageWriter:
    def __init__(self, uow_factory: UnitOfWorkFactory):
        self.uow_factory = uow_factory

    async def persist(
        self,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        data: object,
    ) -> Message:
        if isinstance(data, MessageDraft):
            draft = data
        else:
            draft = MessageDraft.of_text(str(data))

        metadata: JsonObject = dict(draft.metadata or {})
        metadata.pop("author_user_id", None)
        metadata.pop("agent_run_id", None)
        if (
            draft.role == MessageRole.ASSISTANT
            and draft.kind == MessageKind.TEXT
            and "is_final_answer" not in metadata
        ):
            metadata["is_final_answer"] = True
        draft = draft.model_copy(update={"metadata": metadata})

        async with self.uow_factory() as uow:
            repository = ConversationRepository(uow)
            existing = await self._answered_already(
                repository,
                conversation_id=conversation_id,
                draft=draft,
            )
            if existing is not None:
                return existing
            draft = await self._unique_call_id(
                repository,
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                draft=draft,
            )
            return await repository.append_message(
                conversation_id=conversation_id,
                agent_run_id=agent_run_id,
                draft=draft,
            )

    async def notice(
        self, *, conversation_id: UUID, text: str, metadata: JsonObject
    ) -> Message:
        """A line for the person reading the conversation, in no run.

        Run-less on purpose: the turn writing it does not carry it to its own
        model (history takes only the run-less notifications older than the
        turn's first message), and later turns read it as context.
        """
        async with self.uow_factory() as uow:
            return await ConversationRepository(uow).append_message(
                conversation_id=conversation_id,
                agent_run_id=None,
                draft=MessageDraft.of_notification(text, metadata=metadata),
            )

    @staticmethod
    async def _unique_call_id(
        repository: ConversationRepository,
        *,
        conversation_id: UUID,
        agent_run_id: UUID,
        draft: MessageDraft,
    ) -> MessageDraft:
        """A call whose id the conversation already holds, renamed.

        A call is paired with its return by tool call id, per conversation --
        the transcript's cards, a pause's decision row, the synthesized return
        a run's ending writes -- and the model's provider picks the id. One
        that numbers its calls (``call_0``, ``functions.exec:0``) hands a later
        call the id of one already answered: the new call showed the old
        result, a new card vanished behind an old return, a decision adopted
        the old pause. So a clash is renamed here, once, the way calls recorded
        over MCP already get an id of Lemma's own. Only this run's in-memory
        state knows the provider's id; the pump translates what still carries
        it -- the call's return, a pause's WAITING event.

        The same call stored again by the run that stored it (an Agent Host
        replaying its own record) keeps its id, as before. Not ``wait_for``:
        its wait row keeps the id the tool was called with, and the wake writes
        the return under that.
        """
        if (
            draft.kind is not MessageKind.TOOL_CALL
            or draft.tool_name == WAIT_TOOL_NAME
            or not draft.tool_call_id
        ):
            return draft
        earlier = await repository.get_tool_call(
            conversation_id=conversation_id, tool_call_id=draft.tool_call_id
        )
        if earlier is None or _restates(earlier, draft, agent_run_id):
            return draft
        renamed = f"{draft.tool_call_id}-{agent_run_id.hex[-12:]}"
        if await repository.get_tool_call(
            conversation_id=conversation_id, tool_call_id=renamed
        ):
            # The provider reused the id more than once in this run.
            renamed = f"{draft.tool_call_id}-{uuid4().hex[:12]}"
        logger.info(
            "agent.run_message_writer.call_id_renamed.observed",
            conversation_id=str(conversation_id),
            agent_run_id=str(agent_run_id),
            tool_name=draft.tool_name,
        )
        return draft.model_copy(update={"tool_call_id": renamed})

    @staticmethod
    async def _answered_already(
        repository: ConversationRepository,
        *,
        conversation_id: UUID,
        draft: MessageDraft,
    ) -> Message | None:
        """The real return for a call a synthesized one is about to close.

        A run ending closes every tool call it left open, so a user who answered
        an approval and *then* watched the run fail would get a second return
        for it — the card flipping back from what they decided to "the run ended
        before this was answered". A synthesized return never overwrites a real
        one; it exists precisely for the calls that never got one.
        """
        if draft.kind != MessageKind.TOOL_RETURN or not draft.tool_call_id:
            return None
        if not (draft.metadata or {}).get("synthetic_tool_return"):
            return None
        return await repository.get_tool_return(
            conversation_id=conversation_id,
            tool_call_id=draft.tool_call_id,
        )

    async def last_words(self, agent_run_id: UUID) -> Message | None:
        """The answer this run left on the record without naming it, if any.

        Its last assistant text, unless a tool result follows it. A result
        means the agent went on working once it had spoken, so the text was
        leading up to that work and the run ended with no answer. Nothing else
        after it is work: a call announced after the agent's closing words --
        an Agent Host releases one its adapter opened and never settled only
        once the turn is over -- never ran, and the return made up for it as
        the run ended says exactly that; a thought is not speech, and a
        person's message is not the agent's doing.

        Read from the record rather than the events this process saw, so a run
        resumed on another worker after its closing words still finds them.
        """
        async with self.uow_factory() as uow:
            words = await last_text(uow.session, agent_run_id=agent_run_id)
            if words is None or not (words.text or "").strip():
                return None
            if await tool_result_after(
                uow.session, agent_run_id=agent_run_id, sequence=words.sequence
            ):
                return None
        return words

    def answer_output(self, answer: Message) -> JsonValue | None:
        """What a run answered by ``answer`` hands back, by the rule for any answer."""
        draft = MessageDraft.of_text(
            answer.text or "",
            metadata={**(answer.metadata or {}), "is_final_answer": True},
        )
        return self.output_data_from_event(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=draft,
                agent_run_id=answer.agent_run_id,
            )
        )

    def output_data_from_event(self, event: AgentEvent) -> JsonValue | None:
        """Extract terminal output from a MESSAGE event, if present."""

        data = event.data
        if not isinstance(data, MessageDraft):
            return None

        metadata = data.metadata or {}
        if metadata.get("is_final_answer") is False:
            return None
        is_assistant_text = (
            data.role == MessageRole.ASSISTANT and data.kind == MessageKind.TEXT
        )
        if not metadata.get("is_final_answer") and not is_assistant_text:
            return None

        structured_output = metadata.get("structured_output")
        if metadata.get("is_final_answer") and structured_output is not None:
            return structured_output
        if structured_output is not None:
            if isinstance(structured_output, dict):
                return structured_output
            if isinstance(structured_output, str):
                return {"answer": structured_output}
            return {"result": structured_output}

        if is_assistant_text:
            return {"answer": data.text or ""}
        return None

    def final_status_from_event(
        self,
        event: AgentEvent,
    ) -> tuple[ConversationStatus | None, str | None]:
        data = event.data
        if not isinstance(data, MessageDraft):
            return None, None
        metadata = data.metadata or {}
        raw_status = metadata.get("final_answer_status")
        if raw_status is None:
            return None, None
        error = metadata.get("final_answer_error")
        return ConversationStatus(str(raw_status)), str(error) if error else None
