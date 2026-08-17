"""Normalize Agent Host events into the existing runtime event stream.

Events arrive on one ordered per-run Redis Stream, so this applies them in
order and never reconciles two sources. The previous two-lane design needed an
authoritative-sequence map, a per-stream sealed-length counter, and a
``startswith`` delta repair purely to survive a lossy chunk lane writing into
the same accumulated text as the durable lane. None of that exists here: with a
single ordered lane, a chunk cannot arrive after the upsert that supersedes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.agent_host import AgentHostEventType, AgentHostRunState
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
from app.modules.agent.infrastructure.harnesses.agent_host_event_text import (
    _Segment,
    _integer,
    _number,
    error_event,
    event_text,
    is_terminal_event,
    terminal_event,
)
from app.modules.agent.infrastructure.harnesses.streaming import TextStreamBuffer
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    missing_tool_return_events,
)
from app.modules.agent.infrastructure.harnesses.agent_host_tool_calls import (
    ToolCallLedger,
)
from app.modules.agent.infrastructure.harnesses.agent_host_tool_payload import (
    bounded_tool_value,
    first_present,
    json_object,
    tool_metadata,
    tool_name_from_payload,
    unwrap_mcp_content,
)
from app.modules.agent.infrastructure.harnesses.agent_host_final_answer_stream import (
    final_answer_metadata,
    final_answer_record,
    infer_final_answer,
)
from app.modules.agent.tools.final_answer.final_answer_text import final_answer_text
from app.modules.agent.tools.final_answer.final_answer_toolset import (
    FINAL_ANSWER_MARKER,
)
from app.modules.usage.contracts import AgentRunUsage

# Re-exported: the harness imports the whole event vocabulary from this
# module, and moving where a helper lives should not move where it is
# imported from.
__all__ = [
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
        self._message = _Segment(kind="text")
        self._thought = _Segment(kind="thinking")
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

        Overrides anything inferred from the event stream: ACP tool calls carry
        no tool name, so stream recognition is a heuristic while this is the
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

        if event_type is AgentHostEventType.AGENT_MESSAGE_CHUNK:
            return self._chunk(self._message, row, payload)
        if event_type is AgentHostEventType.AGENT_MESSAGE_UPSERT:
            return self._upsert(self._message, row, payload)
        if event_type is AgentHostEventType.AGENT_THOUGHT_CHUNK:
            return self._chunk(self._thought, row, payload)
        if event_type is AgentHostEventType.AGENT_THOUGHT_UPSERT:
            return self._upsert(self._thought, row, payload)
        if event_type is AgentHostEventType.TOOL_CALL_UPSERT:
            call_id = self.tool_calls.object_id(row.object_id, opening=True)
            announced = self.tool_calls.open(
                call_id,
                payload,
                {**metadata, "agent_host_object_id": call_id},
                sequence=row.sequence,
            )
            return self._announce_tool_call(announced)
        if event_type is AgentHostEventType.TOOL_CALL_UPDATE:
            call_id = self.tool_calls.object_id(row.object_id, opening=False)
            return self._tool_call_update(
                row, call_id, payload, {**metadata, "agent_host_object_id": call_id}
            )
        if event_type is AgentHostEventType.USAGE_UPDATE:
            return [*self._drain_tokens(), self._usage_update(row, payload, metadata)]
        if event_type in {
            AgentHostEventType.RUN_STATE,
            AgentHostEventType.PLAN_UPSERT,
            AgentHostEventType.CONFIG_UPDATE,
        }:
            return [
                *self._drain_tokens(),
                self._status(row, event_type.value, payload, metadata),
            ]
        if event_type is AgentHostEventType.PERMISSION_REQUEST:
            return self._permission_request(row, payload, metadata)
        if event_type is AgentHostEventType.TERMINAL:
            return self._terminal(row, payload)
        return []

    # ------------------------------------------------------------------ text

    def _chunk(
        self,
        segment: _Segment,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        rendered = event_text(payload)
        if not rendered and row.payload:
            # Rich content (an image block, say) renders to markdown through
            # the artifact writer; without it there is nothing to append.
            return []
        segment.append(rendered, row.object_id)
        return self._stream_delta(rendered, kind=segment.kind)

    def _upsert(
        self,
        segment: _Segment,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        """Apply one sealed segment.

        The host sends these at every segment boundary so the durable lane
        alone rebuilds the transcript, which means a message that contains a
        tool call arrives as several. See :class:`_Segment`.
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

    def _announce_tool_call(
        self, announced: tuple[MessageDraft, int] | None
    ) -> list[AgentEvent]:
        """Turn a call the ledger decided is ready into a durable message.

        The ledger owns *when*; this owns *how*, which is why the draft carries
        the sequence it was first reported at rather than the one that happened
        to settle it — a call belongs where the agent made it.
        """
        if announced is None:
            return []
        draft, sequence = announced
        return [
            *self._drain_tokens(),
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=draft,
                agent_run_id=self.agent_run_id,
                sequence=sequence,
            ),
        ]

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
        events = self._drain_tokens()
        thought, thought_id = self._thought.take()
        if thought:
            events.append(
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_thinking(
                        thought,
                        metadata={
                            "agent_host_object_id": thought_id,
                            "is_final_answer": False,
                        },
                    ),
                    agent_run_id=self.agent_run_id,
                )
            )
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
                message = message or final_answer_text(
                    record.get("output"), fallback=record.get("error")
                )
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

    def _tool_call_update(
        self,
        row: AgentHostEventEnvelope,
        object_id: str,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        status = str(payload.get("status") or "").upper()
        if status not in {"COMPLETED", "FAILED", "CANCELLED", "DENIED"}:
            # Not a closing update. Claude Code sends the complete `rawInput`
            # here, with no status at all, once the model finishes writing it —
            # so dropping these was what left every streamed tool call showing
            # `{}` for its arguments.
            return self._announce_tool_call(
                self.tool_calls.refine(object_id, payload, metadata)
            )
        if object_id in self.tool_calls.closed:
            return []
        tool_name = self.tool_calls.open_calls.get(
            object_id, tool_name_from_payload(payload)
        )
        # A call still holding its arguments has to be announced before its
        # return, or the conversation gets a result for something it never saw
        # start.
        opening = self._announce_tool_call(self.tool_calls.release(object_id))
        self.tool_calls.close(object_id)
        raw_result = first_present(payload, "result", "rawOutput")
        if status == "COMPLETED":
            # Read the RAW value, before bounding. `_bounded_tool_value` truncates
            # long strings and replaces anything past `_MAX_TOOL_VALUE_DEPTH` with
            # a placeholder — which would turn a valid structured answer into
            # something that still looks structured but is not.
            record = final_answer_record(raw_result) or final_answer_record(
                first_present(payload, "arguments", "args", "rawInput")
            )
            if record is None:
                record = final_answer_record(event_text(payload))
            if record is not None:
                self.adopt_final_answer(record, tool_call_id=object_id)
        result = bounded_tool_value(unwrap_mcp_content(raw_result))
        if status != "COMPLETED":
            result = {
                "success": False,
                "error": str(payload.get("error") or status.lower()),
            }
        return [
            *opening,
            *self._drain_tokens(),
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_return(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_result=result,
                    metadata=tool_metadata(metadata, payload),
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            ),
        ]

    def close_outstanding(self, terminal: AgentEvent) -> list[AgentEvent]:
        # Held calls first: a run that ends mid-turn still has to show what the
        # agent had started, and each one needs its opening on the record before
        # `missing_tool_return_events` synthesizes the return that closes it.
        released = [
            event
            for announced in self.tool_calls.release_all()
            for event in self._announce_tool_call(announced)
        ]
        outstanding = self.tool_calls.outstanding()
        return [
            *released,
            *missing_tool_return_events(
                outstanding_tool_calls=outstanding,
                terminal_event=terminal,
            ),
        ]

    # ---------------------------------------------------------------- other

    def _usage_update(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> AgentEvent:
        usage = json_object(payload.get("usage")) or payload
        return AgentEvent(
            type=AgentEventType.USAGE,
            data=AgentRunUsage(
                model_name=str(usage.get("model_name") or self.model_name),
                input_tokens=_integer(usage.get("input_tokens")),
                output_tokens=_integer(usage.get("output_tokens")),
                request_count=_integer(usage.get("request_count"), default=1),
                tool_call_count=_integer(usage.get("tool_call_count")),
                units=_number(usage.get("units")),
                metadata=metadata,
            ),
            agent_run_id=self.agent_run_id,
            sequence=row.sequence,
        )

    def _status(
        self,
        row: AgentHostEventEnvelope,
        status_value: str,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.STATUS,
            data={"status": status_value, "detail": payload, **metadata},
            agent_run_id=self.agent_run_id,
            sequence=row.sequence,
        )

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
        """
        request_id = row.object_id or f"permission-{row.sequence}"
        tool_call = payload.get("toolCall")
        # Tracked like any other open call, so a run that ends without an answer
        # closes it. An approval card outlives its run otherwise: the host is no
        # longer holding the request — its own timeout denied it — but the card
        # still offers buttons, and pressing one lands on a run that ended hours
        # ago. Answering it *does* write a return, and a synthesized return
        # never replaces a real one, so this closes only the abandoned ones.
        self.tool_calls.open_calls[permission_approval_tool_call_id(request_id)] = (
            "request_approval"
        )
        return [
            *self._flush_messages(final=False),
            *permission_approval_events(
                agent_run_id=self.agent_run_id,
                request_id=request_id,
                sequence=row.sequence,
                payload=payload,
                metadata=metadata,
                # The call this request interrupts was already reported, and its
                # name resolved; prefer the name that call is showing under to
                # the ACP category the permission payload carries.
                tool_name=self.tool_calls.open_calls.get(request_id)
                or (
                    tool_name_from_payload(tool_call)
                    if isinstance(tool_call, dict)
                    else None
                ),
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
    ) -> list[AgentEvent]:
        events = self._flush_messages(final=True)
        terminal = error_event(
            self.agent_run_id,
            "Agent Host reached terminal checkpoint "
            f"{state.value} without its required terminal event",
        )
        events.extend(self.close_outstanding(terminal))
        events.append(terminal)
        return events
