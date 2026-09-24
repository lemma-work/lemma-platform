"""Map Agent Host events onto the existing runtime event stream.

Events arrive on one ordered per-run Redis Stream, so this applies them in
order and never reconciles two sources. The previous two-lane design needed an
authoritative-sequence map, a per-stream sealed-length counter, and a
``startswith`` delta repair purely to survive a lossy chunk lane writing into
the same accumulated text as the durable lane. None of that exists here: with a
single ordered lane, a chunk cannot arrive after the upsert that supersedes it.

Every event arrives already normalized (docs/architecture/agent-host-events.md).
The host knows which adapter and which pinned version it is running, so it names
each tool, shapes its input and output, and says whose tool it is. This module
used to do that itself from raw ACP payloads, and guessed: every Codex MCP call
became ``exec_command``, Claude Code's ``Bash`` never did, and every
``usage_update`` was billed as a request with zero tokens. What is left here is
*mapping*, one event to its messages, plus the conversation-level behaviours
only this side can own: sealing narration, flushing thought per step, finding
the final answer, and closing what a run leaves open.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostEventType,
    AgentHostRunState,
    AgentHostToolCallPayload,
    AgentHostToolResultPayload,
    AgentHostToolStatus,
)
from app.modules.agent.domain.agent_host_permissions import (
    permission_approval_events,
    permission_approval_tool_call_id,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.infrastructure.harnesses.agent_host.event_text import (
    Segment,
    error_event,
    event_text,
    is_terminal_event,
    narration_event,
    no_terminal_message,
    status_event,
    terminal_event,
    thought_event,
    usage_event,
)
from app.modules.agent.infrastructure.harnesses.streaming import TextStreamBuffer
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    missing_tool_return_events,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_calls import (
    OpenToolCall,
    ToolCallLedger,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_events import (
    TOOL_OUTPUT_TOKEN_KIND,
    final_answer_in,
    left_to_lemma,
    open_tool_call,
    parsed_payload,
    tool_call_events,
    tool_output_token,
    tool_return_event,
)
from app.modules.agent.infrastructure.harnesses.agent_host.final_answer_stream import (
    final_answer_metadata,
    infer_final_answer,
)
from app.modules.agent.tools.final_answer.final_answer_text import final_answer_text
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_MARKER,
)

logger = get_logger(__name__)

# Re-exported: the harness imports the whole event vocabulary from this
# module, and moving where a helper lives should not move where it is
# imported from.
__all__ = [
    "TOOL_OUTPUT_TOKEN_KIND",
    "AgentHostEventEnvelope",
    "AgentHostEventNormalizer",
    "error_event",
    "event_text",
    "is_terminal_event",
    "terminal_event",
]


@dataclass(frozen=True, slots=True)
class AgentHostEventEnvelope:
    """One canonical host event read off the run's stream."""

    sequence: int
    type: str
    object_id: str | None
    payload: JsonObject


class AgentHostEventNormalizer:
    """Convert canonical host events to the existing runtime stream."""

    def __init__(
        self,
        *,
        agent_run_id: UUID,
        model_name: str,
        harness_key: str = "unknown",
        structured_expected: bool = False,
        output_schema: JsonObject | None = None,
    ) -> None:
        self.agent_run_id = agent_run_id
        self.model_name = model_name
        self.harness_key = harness_key
        self._message = Segment(kind="text")
        self._thought = Segment(kind="thinking")
        self.token_buffer = TextStreamBuffer()
        self._token_kind: str | None = None
        self.tool_calls = ToolCallLedger()
        # Whether this run owes a structured final answer, and against what.
        # Gates the text fallback: scraping JSON out of an ordinary chat reply
        # would invent structured results that were never claimed.
        self.structured_expected = structured_expected
        self.output_schema = output_schema
        self._final_answer: JsonObject | None = None
        self._final_answer_tool_call_id: str | None = None

    def adopt_final_answer(
        self, record: JsonObject | None, *, tool_call_id: str | None = None
    ) -> None:
        """Take the authoritative final answer recorded by the tool itself.

        Overrides anything read from the event stream: recognising the answer
        in a result is by the marker the tool stamps on it, while this is the
        tool's own record of what it returned.
        """
        if isinstance(record, dict) and record.get(FINAL_ANSWER_MARKER) is True:
            self._final_answer = record
            if tool_call_id is not None:
                self._final_answer_tool_call_id = tool_call_id

    def normalize(
        self,
        row: AgentHostEventEnvelope,
        *,
        payload_override: JsonObject | None = None,
    ) -> list[AgentEvent]:
        event_type = AgentHostEventType(row.type)
        payload = payload_override if payload_override is not None else row.payload
        object_id = row.object_id or f"event-{row.sequence}"
        metadata = {
            "agent_host_object_id": object_id,
            "agent_host_sequence": row.sequence,
            "harness_key": self.harness_key,
        }

        match event_type:
            case AgentHostEventType.AGENT_MESSAGE_CHUNK:
                return self._chunk(self._message, row, payload)
            case AgentHostEventType.AGENT_MESSAGE_UPSERT:
                return self._upsert(self._message, row, payload)
            case AgentHostEventType.AGENT_THOUGHT_CHUNK:
                return self._chunk(self._thought, row, payload)
            case AgentHostEventType.AGENT_THOUGHT_UPSERT:
                return self._upsert(self._thought, row, payload)
            case AgentHostEventType.TOOL_CALL:
                return self._tool_call(row, object_id, metadata)
            case AgentHostEventType.TOOL_CALL_PROGRESS:
                return self._progress(object_id, row.payload)
            case AgentHostEventType.TOOL_CALL_RESULT:
                return self._tool_call_result(row, object_id)
            case AgentHostEventType.USAGE:
                return [
                    *self._drain_tokens(),
                    usage_event(
                        agent_run_id=self.agent_run_id,
                        model_name=self.model_name,
                        payload=row.payload,
                        metadata=metadata,
                        sequence=row.sequence,
                    ),
                ]
            case (
                AgentHostEventType.RUN_STATE
                | AgentHostEventType.SESSION_UPDATE
                | AgentHostEventType.CONFIG_UPDATE
            ):
                return [
                    *self._drain_tokens(),
                    status_event(
                        agent_run_id=self.agent_run_id,
                        status=event_type.value,
                        payload=payload,
                        metadata=metadata,
                        sequence=row.sequence,
                    ),
                ]
            case AgentHostEventType.PERMISSION_REQUEST:
                return self._permission_request(row, payload, metadata)
            case AgentHostEventType.TERMINAL:
                return self._terminal(row, payload)
        return []

    # ------------------------------------------------------------------ text

    def _chunk(
        self,
        segment: Segment,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        rendered = event_text(payload)
        if not rendered and row.payload:
            # Rich content (an image block, say) renders to markdown through
            # the artifact writer; without it there is nothing to append.
            return []
        # What the host itself counts as this chunk's text. Anything the
        # artifact writer added beyond it is rich content the host's upsert will
        # never contain, and has to be kept apart from the text it does (see
        # ``Segment.inserts``).
        host_text = event_text(row.payload)
        if rendered.startswith(host_text):
            rich = rendered[len(host_text) :]
        else:
            host_text, rich = "", rendered
        segment.append(host_text, row.object_id, rich=rich)
        return self._stream_delta(rendered, kind=segment.kind)

    def _upsert(
        self,
        segment: Segment,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        """Apply one sealed segment.

        The host sends these at every segment boundary so the durable lane
        alone rebuilds the transcript, which means a message that contains a
        tool call arrives as several. See :class:`Segment`.
        """
        delta = segment.seal(event_text(payload), row.object_id)
        return self._stream_delta(delta, kind=segment.kind)

    def _stream_delta(self, text: str, *, kind: str) -> list[AgentEvent]:
        if not text:
            return []
        events = self._drain_tokens() if self._token_kind not in {None, kind} else []
        self._token_kind = kind
        events.extend(
            self._token(chunk, kind=kind) for chunk in self.token_buffer.append(text)
        )
        return events

    def _token(self, text: str, *, kind: str) -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": kind, "data": text},
            agent_run_id=self.agent_run_id,
        )

    def _drain_tokens(self) -> list[AgentEvent]:
        kind = self._token_kind
        if kind is None:
            return []
        events = [
            self._token(chunk, kind=kind)
            for chunk in self.token_buffer.drain(force=True)
        ]
        self._token_kind = None
        return events

    def _flush_narration(self) -> list[AgentEvent]:
        """Seal what the agent has said so far as a message of its own."""
        text, object_id = self._message.take()
        if not text:
            return []
        return [
            narration_event(
                agent_run_id=self.agent_run_id, text=text, object_id=object_id
            )
        ]

    def _flush_thought(self) -> list[AgentEvent]:
        """Seal the agent's reasoning for this step as a message of its own.

        Called at every step boundary -- each tool call, each permission
        request, the end of the turn. It used to run only at the end, so a run
        showed a single "Thought" after all of its tool calls, holding every
        step's reasoning glued together, instead of the reasoning before each.
        """
        thought, thought_id = self._thought.take()
        if not thought:
            return []
        return [
            thought_event(
                agent_run_id=self.agent_run_id, text=thought, object_id=thought_id
            )
        ]

    def _flush_step(self) -> list[AgentEvent]:
        """Everything the agent said and thought before reaching for a tool.

        Whatever the agent said before a tool call is its own message, sealed
        here. Accumulating across the whole run instead produced one text
        message per run containing every narration glued together -- and a
        fifty-eight step run rendered as a single paragraph reading "Loading
        schemas first.Schemas loaded. Starting the test sweep.Empty pod", with
        the actual answer welded onto the end of it.

        Tokens drain first: a client clears live text when a message lands.
        """
        return [
            *self._drain_tokens(),
            *self._flush_thought(),
            *self._flush_narration(),
        ]

    def _flush_messages(
        self,
        *,
        final: bool,
        discard_text: bool = False,
    ) -> list[AgentEvent]:
        """Emit accumulated text as messages.

        Only the terminal flush marks its message as the final answer. A run
        that pauses mid-way and resumes would otherwise produce several
        messages all claiming to be the final one.

        ``discard_text`` drops the accumulated *message* while still taking it,
        so nothing leaks into a later flush. Reasoning is unaffected: a thought
        is never mistaken for an answer, and losing it would lose the only
        record of what the agent was doing when it failed.
        """
        events = [*self._drain_tokens(), *self._flush_thought()]
        message, message_id = self._message.take()
        if discard_text:
            message = ""
        metadata: JsonObject = {
            "agent_host_object_id": message_id,
            "is_final_answer": final,
        }
        if final:
            # Only the terminal flush consumes the record. A run that pauses for
            # a permission prompt flushes with final=False and must not burn it.
            record = self._final_answer or self._infer_final_answer(message)
            if record is not None:
                metadata.update(final_answer_metadata(record))
                if self._final_answer_tool_call_id is not None:
                    metadata["tool_call_id"] = self._final_answer_tool_call_id
                answer = final_answer_text(
                    record.get("output"), fallback=record.get("error")
                )
                if self._final_answer is not None and answer:
                    # The tool's answer *is* the answer -- the agent is told to
                    # end the task by calling it. This used to be `message or
                    # answer`, so any trailing text at all won instead, and on a
                    # long run the accumulated narration was never empty: the
                    # report the agent had written was dropped from the body and
                    # survived only in metadata.
                    message = answer
                else:
                    # Inferred rather than called: the record was read back out
                    # of this very text, so the text is the better rendering of
                    # it and `answer` is only a fallback for an empty one.
                    message = message or answer
        if message:
            events.append(
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_text(
                        message,
                        metadata=metadata,
                    ),
                    agent_run_id=self.agent_run_id,
                )
            )
        return events

    def _infer_final_answer(self, message: str) -> JsonObject | None:
        """Last resort: read the answer out of the agent's own final text.

        Gated on this run actually owing a structured answer — an ordinary chat
        reply that happens to contain JSON is never scraped.
        """
        if not self.structured_expected or not message:
            return None
        return infer_final_answer(message, output_schema=self.output_schema)

    # ------------------------------------------------------------- tool calls

    def _tool_call(
        self,
        row: AgentHostEventEnvelope,
        call_id: str,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        """One call, announced once, with its final arguments.

        No holding and no refining: the host releases a call only once its
        arguments have settled (docs/architecture/agent-host-events.md), so the
        first report is the only one and it is already right. The thought and
        narration before it are sealed first, so each step reads in order.
        """
        call = self._parsed(AgentHostToolCallPayload, row, call_id)
        if call is None or self.tool_calls.known(call_id):
            # A duplicate would put a second card on the record for one call; a
            # malformed one has nothing to put there.
            return self._drain_tokens()
        step = self._flush_step()
        if left_to_lemma(call.tool):
            self.tool_calls.drop(call_id)
            return step
        opened = open_tool_call(call, metadata)
        self.tool_calls.open(call_id, opened)
        return [
            *step,
            *tool_call_events(
                agent_run_id=self.agent_run_id,
                call_id=call_id,
                call=opened,
                sequence=row.sequence,
            ),
        ]

    def _progress(self, call_id: str, payload: JsonObject) -> list[AgentEvent]:
        text = event_text(payload)
        if not text or call_id in self.tool_calls.dropped:
            return []
        return [
            *self._drain_tokens(),
            tool_output_token(
                agent_run_id=self.agent_run_id, call_id=call_id, text=text
            ),
        ]

    def _tool_call_result(
        self, row: AgentHostEventEnvelope, call_id: str
    ) -> list[AgentEvent]:
        if call_id in self.tool_calls.dropped:
            # The other half of the pausing-tool drop: with no call on the
            # record under this id, a return under it pairs with nothing. What
            # the model was actually told -- a park's answer, a wait's "you are
            # waiting" -- is written against the id Lemma owns, by whatever
            # resolved it.
            return self._drain_tokens()
        result = self._parsed(AgentHostToolResultPayload, row, call_id)
        call = self.tool_calls.close(call_id) if result is not None else None
        if result is None or call is None:
            if result is not None:
                # The host opens any call it is asked to close, so this cannot
                # happen from a well-behaved host. Writing the return anyway is
                # what used to leave results in conversations that paired with
                # no call at all.
                logger.warning(
                    "agent.harnesses.agent_host.unpaired_tool_result.degraded",
                    agent_run_id=str(self.agent_run_id),
                    tool_call_id=call_id,
                    agent_host_sequence=row.sequence,
                )
            return self._drain_tokens()
        if result.status is AgentHostToolStatus.COMPLETED:
            record = final_answer_in(result.output, call.input)
            if record is not None:
                self.adopt_final_answer(record, tool_call_id=call_id)
        return [
            *self._drain_tokens(),
            tool_return_event(
                agent_run_id=self.agent_run_id,
                call_id=call_id,
                call=call,
                result=result,
                sequence=row.sequence,
            ),
        ]

    def _parsed[ModelT: BaseModel](
        self, model: type[ModelT], row: AgentHostEventEnvelope, call_id: str
    ) -> ModelT | None:
        return parsed_payload(
            model,
            row.payload,
            agent_run_id=self.agent_run_id,
            call_id=call_id,
            sequence=row.sequence,
        )

    def close_outstanding(self, terminal: AgentEvent) -> list[AgentEvent]:
        """Close every call the run left open, so none spins forever."""
        return missing_tool_return_events(
            outstanding_tool_calls=self.tool_calls.outstanding(),
            terminal_event=terminal,
        )

    # ---------------------------------------------------------------- other

    def _permission_request(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        """Turn a permission request into an ordinary Lemma approval.

        Rendering it as a ``request_approval`` call is what lets the web client,
        Slack, Teams and Telegram all show and resolve it with the machinery they
        already have; see ``domain.agent_host_permissions`` for the shape and for
        why this pause emits no WAITING event.

        The host releases the call this request gates before sending it, so the
        call is already on the record and the approval card follows it.
        """
        request_id = row.object_id or f"permission-{row.sequence}"
        gated = payload.get("tool_call_id")
        gated_call = (
            self.tool_calls.calls.get(gated) if isinstance(gated, str) else None
        )
        # Tracked like any other open call, so a run that ends without an answer
        # closes it. An approval card outlives its run otherwise: the host is no
        # longer holding the request — its own timeout denied it — but the card
        # still offers buttons, and pressing one lands on a run that ended hours
        # ago. Answering it *does* write a return, and a synthesized return
        # never replaces a real one, so this closes only the abandoned ones.
        self.tool_calls.open(
            permission_approval_tool_call_id(request_id),
            OpenToolCall(name="request_approval"),
        )
        return [
            *self._flush_messages(final=False),
            *permission_approval_events(
                agent_run_id=self.agent_run_id,
                request_id=request_id,
                sequence=row.sequence,
                payload=payload,
                metadata=metadata,
                # The name the gated call is showing under, so the card and the
                # call it interrupts say the same word.
                tool_name=gated_call.name if gated_call is not None else None,
            ),
        ]

    def _terminal(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        # The host says so when its terminal message is a rewrite of text the
        # adapter already streamed -- a signed-out agent reports itself once as
        # ordinary assistant text and again as the reason the turn ended. Both
        # reached the transcript, so the user saw the failure twice, in two
        # different voices.
        #
        # Dropping the streamed half is not only tidier. Retry is offered only
        # on a failed run whose messages are all the user's
        # (`AgentRun.is_safely_retryable`), so persisting the adapter's own
        # error as an assistant message is what took the button away from the
        # one failure a retry actually fixes.
        events = self._flush_messages(
            final=True,
            discard_text=bool(payload.get("supersedes_stream")),
        )
        terminal = terminal_event(
            agent_run_id=self.agent_run_id,
            state=str(payload.get("state") or payload.get("status") or "FAILED"),
            payload=payload,
            sequence=row.sequence,
        )
        events.extend(self.close_outstanding(terminal))
        events.append(terminal)
        return events

    def finish_without_terminal(
        self,
        *,
        state: AgentHostRunState,
        detail: str | None = None,
    ) -> list[AgentEvent]:
        """End a run whose lease terminalized without a terminal event.

        ``detail`` is what the lease recorded, and it wins: a run the Agent
        Host refused before it started journals nothing at all, so that one
        sentence is the whole of what reaches the person who pressed send.
        """
        events = self._flush_messages(final=True)
        terminal = error_event(self.agent_run_id, detail or no_terminal_message(state))
        events.extend(self.close_outstanding(terminal))
        events.append(terminal)
        return events
