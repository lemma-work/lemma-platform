"""Turning one harness event into what the rest of the system sees.

A harness yields tokens, messages, status changes and terminal outcomes. Each
becomes some combination of a realtime frame, a persisted message and — for the
terminal ones — the run's final write. That mapping is the whole job, and it is
the part most likely to be read by someone asking "why did the user not see
this".

Split from the runner because the runner's job is to *drive* the harness; this
is what to do with what comes back. `handle` returns True when the event was
terminal, which is how the driver knows to stop consuming.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator


from app.core.log.log import get_logger
from app.core.text.thinking_tags import ThinkingStreamSplitter
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunUsage,
    AgentRunStatus,
    ConversationStatus,
    JsonValue,
    MessageDraft,
)
from app.modules.agent.services.run_message_writer import split_reasoning_drafts
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.realtime import (
    message_payload,
    publish_conversation_event,
    status_payload,
    token_payload,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_observer_delivery import notify_event
from app.modules.agent.services.run_finalizer import (
    rejected_run_error_message,
)

logger = get_logger(__name__)

#: The retry path's "discard what you have" signal, sent as a token with no
#: payload (`PydanticAIHarness._stream_reset_event`).
_STREAM_RESET_KIND = "stream_reset"


@dataclass(slots=True)
class RunOutcome:
    """What the harness produced, accumulated as its events arrive.

    Mutable and created before the run starts, because the caller reads it from
    a ``finally`` and from the failure path: a run cancelled halfway still has
    whatever the model said before it went, and that is the part worth recording
    on the span.

    ``terminal_seen`` is not the same as "the iterator ended". A harness can keep
    yielding after its terminal event -- trailing usage, a late status -- and the
    run has already been finalized by then, so everything after is dropped rather
    than written twice.
    """

    output_data: JsonValue | None = None
    final_status: ConversationStatus | None = None
    final_error: str | None = None
    usage_data: AgentRunUsage | None = None
    terminal_seen: bool = False


class RunEventPump:
    """Applies harness events to persistence and the realtime channel."""

    def __init__(self, message_writer: Any, finalizer: Any) -> None:
        self.message_writer = message_writer
        self.finalizer = finalizer
        # One run's worth of state. A `<think>` tag can straddle two deltas, so
        # classifying the live stream means remembering where it is; the pump is
        # constructed per run (`handlers.process_agent_run`), so this needs no
        # keying by run id.
        self._token_splitter = ThinkingStreamSplitter()

    async def handle(
        self,
        *,
        event: AgentEvent,
        run: RunIdentity,
        outcome: RunOutcome,
    ) -> bool:
        """True when this event was terminal and the run has been finalized."""
        if event.type != AgentEventType.TOKEN:
            # Anything that is not a token ends the text stream that was being
            # classified, so whatever the splitter was holding back is now
            # decided. Draining here mirrors how the harnesses drain their own
            # character buffers at a part boundary.
            await self._drain_tokens(run)

        if event.type == AgentEventType.TOKEN:
            token_kind = "text"
            token_data = event.data
            if isinstance(event.data, dict):
                raw_kind = event.data.get("kind")
                if raw_kind is not None:
                    token_kind = str(raw_kind)
                token_data = event.data.get("data", "")
            for kind, chunk in self._classify_token(token_kind, str(token_data)):
                await publish_conversation_event(
                    run.conversation_id,
                    token_payload(run.agent_run_id, chunk, kind=kind),
                )
            return False

        if event.type == AgentEventType.MESSAGE:
            saved_message = await self.message_writer.persist(
                conversation_id=run.conversation_id,
                agent_run_id=run.agent_run_id,
                data=event.data,
            )
            # Read back from the run rather than stored on the row: the message
            # table has no agent column, and the frame needs one so the
            # transcript can name whoever is speaking as it arrives.
            saved_message.agent_id = run.agent_id
            await publish_conversation_event(
                run.conversation_id,
                message_payload(run.agent_run_id, message_to_payload(saved_message)),
            )
            return False

        if event.type == AgentEventType.STATUS:
            await publish_conversation_event(
                run.conversation_id,
                status_payload(
                    run.agent_run_id,
                    event.data
                    if isinstance(event.data, dict)
                    else {"status": str(event.data)},
                ),
            )
            return False

        if event.type == AgentEventType.ERROR:
            await self.finalizer.finish(
                status=AgentRunStatus.FAILED,
                error=str(event.data),
                usage_data=outcome.usage_data,
                run=run,
            )
            return True

        if event.type == AgentEventType.REJECTED:
            # The remote harness refused this run before dispatch -- terminal,
            # like ERROR, but with a more actionable message built from the
            # event's structured data.
            await self.finalizer.finish(
                status=AgentRunStatus.FAILED,
                error=rejected_run_error_message(event.data),
                usage_data=outcome.usage_data,
                run=run,
            )
            return True

        if event.type == AgentEventType.WAITING:
            # The agent paused for the user (ask_user / request_approval). Finish
            # this run as COMPLETED but flip the conversation to WAITING — the
            # proven final_answer pause shape. The pending tool call is already
            # persisted; resolving it starts a fresh run that resumes from history.
            await self.finalizer.finish(
                status=AgentRunStatus.COMPLETED,
                conversation_status=ConversationStatus.WAITING,
                output_data=outcome.output_data,
                usage_data=outcome.usage_data,
                run=run,
            )
            return True

        if event.type in {AgentEventType.COMPLETED, AgentEventType.STOPPED}:
            await self.finalizer.finish(
                status=(
                    AgentRunStatus.STOPPED
                    if event.type == AgentEventType.STOPPED
                    else AgentRunStatus.FAILED
                    if outcome.final_status == ConversationStatus.FAILED
                    else AgentRunStatus.COMPLETED
                ),
                conversation_status=outcome.final_status,
                error=outcome.final_error,
                output_data=outcome.output_data,
                usage_data=outcome.usage_data,
                run=run,
            )
            return True

        return False

    async def drive(
        self,
        events: AsyncIterator[AgentEvent],
        *,
        run: RunIdentity,
        outcome: RunOutcome,
        observer: Any,
        conversation: Any,
        ctx: Any,
    ) -> None:
        """Consume the harness's events until it stops producing them.

        The outcome is filled in as they arrive rather than returned, so a
        caller unwinding through an exception still sees everything that had
        been produced up to that point.

        A stream that simply stops without a terminal event is finalized here
        rather than left: see the end of this method.
        """
        async for raw_event in events:
            if outcome.terminal_seen:
                continue
            # Split before anything sees the event, not just before persistence.
            # `_absorb_message` reads the draft to decide the run's output, and
            # observers read it to decide what a surface shows -- so a split
            # applied only at the database would still hand the model's
            # reasoning back as the run's answer.
            should_stop = False
            for event in _split_message_event(raw_event):
                await notify_event(observer, event, conversation, ctx, run.agent_run_id)
                if event.type == AgentEventType.USAGE:
                    outcome.usage_data = _usage_from_event(event) or outcome.usage_data
                    continue
                should_stop = await self.handle(event=event, run=run, outcome=outcome)
                if event.type == AgentEventType.MESSAGE:
                    self._absorb_message(event, outcome)
                if should_stop:
                    break
            if should_stop:
                outcome.terminal_seen = True

        if outcome.terminal_seen:
            return
        # The stream ended without ever saying how the run turned out. Nothing
        # had finalized and nothing raised, so both the run and its conversation
        # were left RUNNING with nothing that would move them: the orphan cron
        # waits an hour and only repairs the run, and whatever is waiting on the
        # conversation — a schedule run, a workflow step — waits forever.
        #
        # FAILED rather than COMPLETED because there is no answer here. The
        # model never said it was done, and handing back an empty result as
        # though it were one is worse than saying the turn did not finish.
        logger.warning(
            "agent.run_event_pump.stream_ended_without_a_terminal_event.degraded",
            agent_run_id=run.agent_run_id,
        )
        outcome.terminal_seen = True
        outcome.final_status = ConversationStatus.FAILED
        await self.finalizer.finish(
            run=run,
            status=AgentRunStatus.FAILED,
            conversation_status=ConversationStatus.FAILED,
            error="The agent stopped without producing a result. Please try again.",
            output_data=outcome.output_data,
            usage_data=outcome.usage_data,
        )

    def _classify_token(self, kind: str, text: str) -> list[tuple[str, str]]:
        """Re-tag reasoning a model wrote into the text stream.

        Only the text lane is examined. A token the harness already called
        thinking or tool is not text, and running it through the splitter would
        be looking for a convention in a channel that does not use it.

        This is the live half of the rule ``split_reasoning_drafts`` applies to
        the durable message. Without it a reader watches the reasoning type
        itself out inside the answer bubble and only sees it move once the
        message lands, which is worse than either outcome on its own.

        Every non-text kind passes through **including an empty one**: the
        retry path signals "discard the partial bubble" as a `stream_reset`
        token with no payload, so dropping empty frames as uninteresting
        silently removes a control frame and leaves the abandoned answer on
        screen with the retried one glued after it.
        """
        if kind == _STREAM_RESET_KIND:
            # The response so far is being abandoned and re-streamed from the
            # start. Whatever the splitter was mid-way through belongs to text
            # the client is about to throw away -- carried into the retry it
            # would classify the replacement answer as a continuation of a
            # thought that no longer exists.
            self._token_splitter = ThinkingStreamSplitter()
            return [(kind, text)]
        if kind != "text":
            return [(kind, text)]
        return list(self._token_splitter.feed(text))

    async def _drain_tokens(self, run: RunIdentity) -> None:
        for kind, chunk in self._token_splitter.flush():
            await publish_conversation_event(
                run.conversation_id,
                token_payload(run.agent_run_id, chunk, kind=kind),
            )

    def _absorb_message(self, event: AgentEvent, outcome: RunOutcome) -> None:
        """Carry forward what a message event says about the run's ending.

        A message can both be the run's output and declare the conversation's
        final status -- a `final_answer` does both -- so this runs after the
        event has already been persisted, and only overwrites what the event
        actually carried.
        """
        saved_output = self.message_writer.output_data_from_event(event)
        if saved_output is not None:
            outcome.output_data = saved_output
        final_status, final_error = self.message_writer.final_status_from_event(event)
        if final_status is not None:
            outcome.final_status = final_status
        if final_error:
            outcome.final_error = final_error


def _split_message_event(event: AgentEvent) -> list[AgentEvent]:
    """One message event, split into the messages it should actually become.

    Identity for everything that is not an assistant message carrying reasoning
    in its text, which is all but a handful of events -- the list is returned
    unchanged rather than rebuilt so the common path allocates nothing.
    """
    if event.type is not AgentEventType.MESSAGE or not isinstance(
        event.data, MessageDraft
    ):
        return [event]
    drafts = split_reasoning_drafts(event.data)
    if len(drafts) == 1 and drafts[0] is event.data:
        return [event]
    return [event.model_copy(update={"data": draft}) for draft in drafts]


def _usage_from_event(event: AgentEvent) -> AgentRunUsage | None:
    """The usage an event carries, however the harness chose to shape it."""
    if isinstance(event.data, AgentRunUsage):
        return event.data
    if isinstance(event.data, dict):
        return AgentRunUsage.model_validate(event.data)
    return None
