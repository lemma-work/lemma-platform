"""Normalize durable Agent Host events into Lemma runtime events."""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.agent_host import AgentHostEventType, AgentHostRunState
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.infrastructure.harnesses.tool_returns import (
    missing_tool_return_events,
)
from app.modules.agent.infrastructure.runtime_models import AgentHostEventModel
from app.modules.usage.contracts import AgentRunUsage


class AgentHostEventNormalizer:
    """Convert durable canonical host events to the existing runtime stream."""

    def __init__(self, *, agent_run_id: UUID, model_name: str) -> None:
        self.agent_run_id = agent_run_id
        self.model_name = model_name
        self.message_text: dict[str, str] = {}
        self.thought_text: dict[str, str] = {}
        self.tool_calls: dict[str, str] = {}
        self.closed_tool_calls: set[str] = set()

    def normalize(self, row: AgentHostEventModel) -> list[AgentEvent]:
        event_type = AgentHostEventType(row.type)
        payload = _json_object(row.payload)
        object_id = row.object_id or f"event-{row.sequence}"
        metadata = {
            "agent_host_object_id": object_id,
            "agent_host_sequence": row.sequence,
            "harness_key": row.harness_key,
            "adapter_version": row.adapter_version,
        }
        if event_type is AgentHostEventType.AGENT_MESSAGE_CHUNK:
            return self._append_chunk(object_id, payload, self.message_text)
        if event_type is AgentHostEventType.AGENT_MESSAGE_UPSERT:
            return self._upsert_text(
                object_id=object_id,
                payload=payload,
                storage=self.message_text,
                kind="text",
            )
        if event_type is AgentHostEventType.AGENT_THOUGHT_CHUNK:
            return self._append_chunk(
                object_id,
                payload,
                self.thought_text,
                kind="thinking",
            )
        if event_type is AgentHostEventType.AGENT_THOUGHT_UPSERT:
            return self._upsert_text(
                object_id=object_id,
                payload=payload,
                storage=self.thought_text,
                kind="thinking",
            )
        if event_type is AgentHostEventType.TOOL_CALL_UPSERT:
            return self._tool_call_upsert(row, object_id, payload, metadata)
        if event_type is AgentHostEventType.TOOL_CALL_UPDATE:
            return self._tool_call_update(row, object_id, payload, metadata)
        if event_type is AgentHostEventType.USAGE_UPDATE:
            return self._usage_update(row, payload, metadata)
        if event_type in {
            AgentHostEventType.RUN_STATE,
            AgentHostEventType.PLAN_UPSERT,
            AgentHostEventType.CONFIG_UPDATE,
            AgentHostEventType.WARNING,
        }:
            return [self._status(row, event_type.value, payload, metadata)]
        if event_type is AgentHostEventType.PERMISSION_REQUEST:
            return self._permission_denied(row, payload, metadata)
        if event_type is AgentHostEventType.INPUT_REQUEST:
            return self._input_request(row, payload, metadata)
        if event_type is AgentHostEventType.TERMINAL:
            return self._terminal(row, payload)
        return []

    def _append_chunk(
        self,
        object_id: str,
        payload: JsonObject,
        storage: dict[str, str],
        *,
        kind: str = "text",
    ) -> list[AgentEvent]:
        text = _event_text(payload)
        storage[object_id] = storage.get(object_id, "") + text
        return [self._token(text, kind=kind)] if text else []

    def _tool_call_upsert(
        self,
        row: AgentHostEventModel,
        object_id: str,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        tool_name = str(payload.get("name") or payload.get("tool_name") or "tool")
        if object_id in self.tool_calls:
            return []
        self.tool_calls[object_id] = tool_name
        return [
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_call(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_args=payload.get("arguments", payload.get("args")),
                    metadata=metadata,
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            )
        ]

    def _tool_call_update(
        self,
        row: AgentHostEventModel,
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
            str(payload.get("name") or payload.get("tool_name") or "tool"),
        )
        self.closed_tool_calls.add(object_id)
        result = payload.get("result")
        if status != "COMPLETED":
            result = {
                "success": False,
                "error": str(payload.get("error") or status.lower()),
            }
        return [
            AgentEvent(
                type=AgentEventType.MESSAGE,
                data=MessageDraft.of_tool_return(
                    tool_name=tool_name,
                    tool_call_id=object_id,
                    tool_result=result,
                    metadata=metadata,
                ),
                agent_run_id=self.agent_run_id,
                sequence=row.sequence,
            )
        ]

    def _usage_update(
        self,
        row: AgentHostEventModel,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        usage = _json_object(payload.get("usage")) or payload
        return [
            AgentEvent(
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
        ]

    def _status(
        self,
        row: AgentHostEventModel,
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
        row: AgentHostEventModel,
        payload: JsonObject,
        metadata: JsonObject,
    ) -> list[AgentEvent]:
        events = self._flush_messages()
        events.append(self._status(row, "permission_request.denied", payload, metadata))
        return events

    def _input_request(
        self,
        row: AgentHostEventModel,
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
        row: AgentHostEventModel,
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

    def _upsert_text(
        self,
        *,
        object_id: str,
        payload: JsonObject,
        storage: dict[str, str],
        kind: str,
    ) -> list[AgentEvent]:
        full_text = _event_text(payload)
        previous = storage.get(object_id, "")
        storage[object_id] = full_text
        if full_text.startswith(previous):
            delta = full_text[len(previous) :]
        elif full_text != previous:
            delta = full_text
        else:
            delta = ""
        return [self._token(delta, kind=kind)] if delta else []

    def _token(self, text: str, *, kind: str = "text") -> AgentEvent:
        return AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": kind, "data": text},
            agent_run_id=self.agent_run_id,
        )

    def _flush_messages(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
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


def _json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def _event_text(payload: JsonObject) -> str:
    return str(
        payload.get("text") or payload.get("delta") or payload.get("content") or ""
    )


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
