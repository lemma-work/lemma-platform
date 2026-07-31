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
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    JsonValue,
    MessageDraft,
)
from app.modules.agent.infrastructure.harnesses.streaming import TextStreamBuffer
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    missing_tool_return_events,
)
from app.modules.usage.contracts import AgentRunUsage


@dataclass(frozen=True, slots=True)
class AgentHostEventEnvelope:
    """One canonical host event read off the run's stream."""

    sequence: int
    type: str
    object_id: str | None
    payload: JsonObject


@dataclass(slots=True)
class _Segment:
    """Accumulated text for one stream kind.

    ``object_id`` is the host's identifier for the current segment, carried so
    the emitted message metadata refers to something real rather than to the
    internal stream name.
    """

    kind: str
    text: str = ""
    object_id: str | None = None

    def append(self, chunk: str, object_id: str | None) -> None:
        self.text += chunk
        if object_id is not None:
            self.object_id = object_id

    def replace(self, full_text: str, object_id: str | None) -> str:
        """Apply an authoritative full-text upsert, returning the new tail."""
        delta = full_text[len(self.text) :] if full_text.startswith(self.text) else ""
        self.text = full_text
        if object_id is not None:
            self.object_id = object_id
        return delta

    def take(self) -> tuple[str, str | None]:
        text, object_id = self.text, self.object_id
        self.text, self.object_id = "", None
        return text, object_id


class AgentHostEventNormalizer:
    """Convert canonical host events to the existing runtime stream."""

    def __init__(
        self,
        *,
        agent_run_id: UUID,
        model_name: str,
        harness_key: str = "unknown",
    ) -> None:
        self.agent_run_id = agent_run_id
        self.model_name = model_name
        self.harness_key = harness_key
        self._message = _Segment(kind="text")
        self._thought = _Segment(kind="thinking")
        self.token_buffer = TextStreamBuffer()
        self._token_kind: str | None = None
        self.tool_calls: dict[str, str] = {}
        self.closed_tool_calls: set[str] = set()

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
            return self._tool_call_upsert(row, object_id, payload, metadata)
        if event_type is AgentHostEventType.TOOL_CALL_UPDATE:
            return self._tool_call_update(row, object_id, payload, metadata)
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
        """Apply a full-text upsert.

        The host sends these at every segment boundary so the durable lane
        alone rebuilds the transcript. Because the lane is ordered, the upsert
        can only ever extend what this normalizer already accumulated, so the
        delta is the tail.
        """
        delta = segment.replace(event_text(payload), row.object_id)
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

    def _flush_messages(self, *, final: bool) -> list[AgentEvent]:
        """Emit accumulated text as messages.

        Only the terminal flush marks its message as the final answer. A run
        that pauses mid-way and resumes would otherwise produce several
        messages all claiming to be the final one.
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
        if message:
            events.append(
                AgentEvent(
                    type=AgentEventType.MESSAGE,
                    data=MessageDraft.of_text(
                        message,
                        metadata={
                            "agent_host_object_id": message_id,
                            "is_final_answer": final,
                        },
                    ),
                    agent_run_id=self.agent_run_id,
                )
            )
        return events

    # ------------------------------------------------------------- tool calls

    def _tool_call_upsert(
        self,
        row: AgentHostEventEnvelope,
        object_id: str,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        tool_name = _tool_name(payload)
        if object_id in self.tool_calls:
            return []
        self.tool_calls[object_id] = tool_name
        events = self._drain_tokens()
        events.append(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_args=_tool_args(payload, tool_name),
                    metadata=_tool_metadata(metadata, payload),
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            )
        )
        return events

    def _tool_call_update(
        self,
        row: AgentHostEventEnvelope,
        object_id: str,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        status = str(payload.get("status") or "").upper()
        if (
            status not in {"COMPLETED", "FAILED", "CANCELLED", "DENIED"}
            or object_id in self.closed_tool_calls
        ):
            return []
        tool_name = self.tool_calls.get(object_id, _tool_name(payload))
        self.closed_tool_calls.add(object_id)
        result = _bounded_tool_value(_first_present(payload, "result", "rawOutput"))
        if status != "COMPLETED":
            result = {
                "success": False,
                "error": str(payload.get("error") or status.lower()),
            }
        return [
            *self._drain_tokens(),
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_return(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_result=result,
                    metadata=_tool_metadata(metadata, payload),
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            ),
        ]

    def close_outstanding(self, terminal: AgentEvent) -> list[AgentEvent]:
        outstanding = {
            tool_call_id: tool_name
            for tool_call_id, tool_name in self.tool_calls.items()
            if tool_call_id not in self.closed_tool_calls
        }
        return missing_tool_return_events(
            outstanding_tool_calls=outstanding,
            terminal_event=terminal,
        )

    # ---------------------------------------------------------------- other

    def _usage_update(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> AgentEvent:
        usage = _json_object(payload.get("usage")) or payload
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
        """Surface a permission request so a human can answer it.

        The run pauses here: the host holds the agent's request open until a
        decision comes back as a command, so this emits WAITING rather than a
        terminal event.
        """
        events = self._flush_messages(final=False)
        events.append(self._status(row, "permission_request", payload, metadata))
        events.append(
            AgentEvent(
                type=AgentEventType.WAITING,
                data={**payload, "agent_host_object_id": row.object_id},
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            )
        )
        return events

    def _terminal(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        events = self._flush_messages(final=True)
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


_MAX_TOOL_STRING_CHARACTERS = 4_096
_MAX_TOOL_COLLECTION_ITEMS = 32
_MAX_TOOL_VALUE_DEPTH = 4


def _first_present(payload: JsonObject, *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _tool_name(payload: JsonObject) -> str:
    for key in ("name", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    kind = payload.get("kind")
    if isinstance(kind, str) and kind.strip():
        normalized = kind.strip().lower()
        return "exec_command" if normalized == "execute" else normalized
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return "tool"


def _tool_metadata(metadata: JsonObject, payload: JsonObject) -> JsonObject:
    result = dict(metadata)
    for source, target in (("title", "tool_title"), ("kind", "tool_kind")):
        value = payload.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value.strip()
    return result


def _tool_args(payload: JsonObject, tool_name: str) -> JsonValue:
    value = _first_present(payload, "arguments", "args", "rawInput")
    if tool_name == "exec_command" and isinstance(value, dict):
        normalized = dict(value)
        command = normalized.pop("command", None)
        if "cmd" not in normalized and isinstance(command, str):
            normalized["cmd"] = command
        value = normalized
    return _bounded_tool_value(value)


def _bounded_tool_value(value: object, *, depth: int = 0) -> JsonValue:
    if depth >= _MAX_TOOL_VALUE_DEPTH:
        return {"omitted": "nested tool payload"}
    if isinstance(value, str):
        if len(value) <= _MAX_TOOL_STRING_CHARACTERS:
            return value
        return {
            "omitted": "large tool payload",
            "character_count": len(value),
        }
    if isinstance(value, dict):
        items = list(value.items())
        result = {
            str(key): _bounded_tool_value(item, depth=depth + 1)
            for key, item in items[:_MAX_TOOL_COLLECTION_ITEMS]
        }
        if len(items) > _MAX_TOOL_COLLECTION_ITEMS:
            result["_omitted_item_count"] = len(items) - _MAX_TOOL_COLLECTION_ITEMS
        return result
    if isinstance(value, list):
        result = [
            _bounded_tool_value(item, depth=depth + 1)
            for item in value[:_MAX_TOOL_COLLECTION_ITEMS]
        ]
        if len(value) > _MAX_TOOL_COLLECTION_ITEMS:
            result.append(
                {"omitted_item_count": len(value) - _MAX_TOOL_COLLECTION_ITEMS}
            )
        return result
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def event_text(payload: JsonObject) -> str:
    for key in ("text", "delta"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return ""


def _integer(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def error_event(agent_run_id: UUID, message: str) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.ERROR,
        data=message,
        agent_run_id=agent_run_id,
    )


def terminal_event(
    *,
    agent_run_id: UUID,
    state: str,
    payload: JsonObject,
    sequence: int | None = None,
) -> AgentEvent:
    normalized = state.upper()
    if normalized == AgentHostRunState.SUCCEEDED.value:
        event_type = AgentEventType.COMPLETED
        data: object = payload
    elif normalized == AgentHostRunState.WAITING_INPUT.value:
        event_type = AgentEventType.WAITING
        data = payload
    elif normalized == AgentHostRunState.CANCELLED.value:
        event_type = AgentEventType.STOPPED
        data = payload
    else:
        event_type = AgentEventType.ERROR
        data = str(
            payload.get("error")
            or payload.get("message")
            or f"Agent Host run ended in {normalized}"
        )
    return AgentEvent(
        type=event_type,
        data=data,
        agent_run_id=agent_run_id,
        sequence=sequence,
    )


def is_terminal_event(event: AgentEvent) -> bool:
    return event.type in {
        AgentEventType.COMPLETED,
        AgentEventType.STOPPED,
        AgentEventType.ERROR,
        AgentEventType.REJECTED,
        AgentEventType.WAITING,
    }
