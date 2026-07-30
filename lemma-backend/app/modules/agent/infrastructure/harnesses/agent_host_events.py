"""Normalize durable and realtime Agent Host events into runtime events."""

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
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    missing_tool_return_events,
)
from app.modules.agent.infrastructure.harnesses.streaming import TextStreamBuffer
from app.modules.agent.infrastructure.runtime_models import AgentHostEventModel
from app.modules.usage.contracts import AgentRunUsage


@dataclass(frozen=True, slots=True)
class AgentHostEventEnvelope:
    """One canonical host event from either lane (journal row or stream)."""

    sequence: int
    type: str
    object_id: str | None
    payload: JsonObject

    @classmethod
    def from_model(cls, row: AgentHostEventModel) -> "AgentHostEventEnvelope":
        return cls(
            sequence=row.sequence,
            type=row.type,
            object_id=row.object_id,
            payload=_json_object(row.payload),
        )


class AgentHostEventNormalizer:
    """Convert canonical host events to the existing runtime stream.

    Full-text upserts are authoritative: once an upsert lands for a stream,
    older chunk events (which travel the lossy realtime lane) are dropped so
    out-of-order delivery can never corrupt the accumulated text.
    """

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
        self.message_text: dict[str, str] = {}
        self.thought_text: dict[str, str] = {}
        self.token_buffer = TextStreamBuffer()
        self.token_buffer_key: tuple[str, str] | None = None
        self.tool_calls: dict[str, str] = {}
        self.closed_tool_calls: set[str] = set()
        self._authoritative_sequence: dict[str, int] = {}
        # Per-stream length of the text sealed by upserts or rich content;
        # only the unsealed tail of a stream may be replaced by an upsert.
        self._sealed_length: dict[str, int] = {}

    def normalize_stream(
        self,
        *,
        sequence: int,
        event_type: AgentHostEventType,
        object_id: str | None,
        payload: JsonObject,
    ) -> list[AgentEvent]:
        """Normalize one cosmetic chunk from the realtime lane.

        Only chunk events travel here; anything else is ignored. Chunks that
        predate the latest full-text upsert for their stream are stale.
        """
        if event_type is AgentHostEventType.AGENT_MESSAGE_CHUNK:
            storage, stream_key, kind = self.message_text, "agent-message", "text"
        elif event_type is AgentHostEventType.AGENT_THOUGHT_CHUNK:
            storage, stream_key, kind = self.thought_text, "agent-thought", "thinking"
        else:
            return []
        if sequence <= self._authoritative_sequence.get(stream_key, 0):
            return []
        return self._append_chunk(
            payload,
            storage,
            stream_key=stream_key,
            kind=kind,
        )

    def normalize(
        self,
        row: AgentHostEventEnvelope,
        *,
        payload_override: JsonObject | None = None,
    ) -> list[AgentEvent]:
        event_type = AgentHostEventType(row.type)
        payload = (
            payload_override
            if payload_override is not None
            else _json_object(row.payload)
        )
        object_id = row.object_id or f"event-{row.sequence}"
        metadata = {
            "agent_host_object_id": object_id,
            "agent_host_sequence": row.sequence,
            "harness_key": self.harness_key,
        }
        if event_type is AgentHostEventType.AGENT_MESSAGE_CHUNK:
            return self._durable_chunk(
                row,
                payload,
                storage=self.message_text,
                stream_key="agent-message",
                kind="text",
            )
        if event_type is AgentHostEventType.AGENT_MESSAGE_UPSERT:
            self._authoritative_sequence["agent-message"] = row.sequence
            return self._upsert_segment(
                payload=payload,
                storage=self.message_text,
                stream_key="agent-message",
                kind="text",
            )
        if event_type is AgentHostEventType.AGENT_THOUGHT_CHUNK:
            return self._durable_chunk(
                row,
                payload,
                storage=self.thought_text,
                stream_key="agent-thought",
                kind="thinking",
            )
        if event_type is AgentHostEventType.AGENT_THOUGHT_UPSERT:
            self._authoritative_sequence["agent-thought"] = row.sequence
            return self._upsert_segment(
                payload=payload,
                storage=self.thought_text,
                stream_key="agent-thought",
                kind="thinking",
            )
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
            AgentHostEventType.WARNING,
        }:
            return [
                *self._drain_tokens(),
                self._status(row, event_type.value, payload, metadata),
            ]
        if event_type is AgentHostEventType.PERMISSION_REQUEST:
            return self._permission_denied(row, payload, metadata)
        if event_type is AgentHostEventType.INPUT_REQUEST:
            return self._input_request(row, payload, metadata)
        if event_type is AgentHostEventType.TERMINAL:
            return self._terminal(row, payload)
        return []

    def _durable_chunk(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        *,
        storage: dict[str, str],
        stream_key: str,
        kind: str,
    ) -> list[AgentEvent]:
        if row.sequence <= self._authoritative_sequence.get(stream_key, 0):
            return []
        original = _json_object(row.payload)
        if not event_text(original) and original:
            # Rich content (e.g. an image block) is durable: append any
            # rendered markdown, then seal the segment so a later upsert
            # cannot replace it.
            rendered = event_text(payload)
            storage[stream_key] = storage.get(stream_key, "") + rendered
            self._sealed_length[stream_key] = len(storage[stream_key])
            return self._stream_delta(rendered, kind=kind)
        return self._append_chunk(payload, storage, stream_key=stream_key, kind=kind)

    def _append_chunk(
        self,
        payload: JsonObject,
        storage: dict[str, str],
        *,
        stream_key: str,
        kind: str = "text",
    ) -> list[AgentEvent]:
        text = event_text(payload)
        storage[stream_key] = storage.get(stream_key, "") + text
        return self._stream_delta(text, kind=kind)

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
        tool_metadata = _tool_metadata(metadata, payload)
        events = self._drain_tokens()
        events.append(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_args=_tool_args(payload, tool_name),
                    metadata=tool_metadata,
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
        tool_name = self.tool_calls.get(
            object_id,
            _tool_name(payload),
        )
        self.closed_tool_calls.add(object_id)
        result = _bounded_tool_value(
            _first_present(payload, "result", "rawOutput")
        )
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
            )
        ]

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

    def _permission_denied(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        events = self._flush_messages()
        events.append(self._status(row, "permission_request.denied", payload, metadata))
        return events

    def _input_request(
        self,
        row: AgentHostEventEnvelope,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        events = self._flush_messages()
        events.append(
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_notification(
                    str(
                        payload.get("prompt")
                        or payload.get("message")
                        or "The local agent is waiting for input."
                    ),
                    metadata={**metadata, "is_final_answer": False},
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            )
        )
        events.append(
            AgentEvent(
                type=AgentEventType.WAITING,
                data=payload,
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
        events = self._flush_messages()
        terminal = terminal_event(
            agent_run_id=self.agent_run_id,
            state=str(payload.get("state") or payload.get("status") or "FAILED"),
            payload=payload,
            sequence=row.sequence,
        )
        events.extend(self.close_outstanding(terminal))
        events.append(terminal)
        return events

    def _upsert_segment(
        self,
        *,
        payload: JsonObject,
        storage: dict[str, str],
        stream_key: str,
        kind: str,
    ) -> list[AgentEvent]:
        """Apply an authoritative full-text upsert to the current segment.

        Text sealed by earlier upserts or rich content is never replaced, so
        replaying only durable events rebuilds the exact transcript; the
        unsealed tail is superseded by the upsert (deduplicating live chunks
        that already streamed for the same segment).
        """
        full_text = event_text(payload)
        current = storage.get(stream_key, "")
        sealed = self._sealed_length.get(stream_key, 0)
        prefix = current[:sealed]
        segment = current[sealed:]
        if full_text.startswith(segment):
            delta = full_text[len(segment) :]
        else:
            delta = full_text
        storage[stream_key] = prefix + full_text
        self._sealed_length[stream_key] = sealed + len(full_text)
        return self._stream_delta(delta, kind=kind)

    def _token(self, text: str, *, kind: str = "text") -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": kind, "data": text},
            agent_run_id=self.agent_run_id,
        )

    def _stream_delta(
        self,
        text: str,
        *,
        kind: str,
    ) -> list[AgentEvent]:
        if not text:
            return []
        key = (kind, f"{kind}-stream")
        events = self._drain_tokens() if self.token_buffer_key not in {None, key} else []
        self.token_buffer_key = key
        events.extend(
            self._token(chunk, kind=kind) for chunk in self.token_buffer.append(text)
        )
        return events

    def _drain_tokens(self) -> list[AgentEvent]:
        key = self.token_buffer_key
        if key is None:
            return []
        kind, _object_id = key
        events = [
            self._token(chunk, kind=kind)
            for chunk in self.token_buffer.drain(force=True)
        ]
        self.token_buffer_key = None
        return events

    def _flush_messages(self) -> list[AgentEvent]:
        events = self._drain_tokens()
        for object_id, text in self.thought_text.items():
            if text:
                events.append(
                    AgentEvent(
                        type=AgentEventType.MESSAGE,
                        data=MessageDraft.of_thinking(
                            text,
                            metadata={
                                "agent_host_object_id": object_id,
                                "is_final_answer": False,
                            },
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                )
        for object_id, text in self.message_text.items():
            if text:
                events.append(
                    AgentEvent(
                        type=AgentEventType.MESSAGE,
                        data=MessageDraft.of_text(
                            text,
                            metadata={
                                "agent_host_object_id": object_id,
                                "is_final_answer": True,
                            },
                        ),
                        agent_run_id=self.agent_run_id,
                    )
                )
        self.thought_text.clear()
        self.message_text.clear()
        return events

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

    def finish_without_terminal(
        self,
        *,
        state: AgentHostRunState,
    ) -> list[AgentEvent]:
        events = self._flush_messages()
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
