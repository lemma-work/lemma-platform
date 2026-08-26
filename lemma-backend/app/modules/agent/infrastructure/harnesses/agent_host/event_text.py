"""Accumulating an agent's streamed text, and reading events back out.

Split from the normalizer because these are the two halves of one contract
the host also implements, in Rust: ``event_text`` mirrors ``runtime::
chunk_text``, and ``Segment`` mirrors the buffer the host seals into an
upsert. ``tests/fixtures/wire_contract.json`` holds both sides to the same
cases, because a disagreement between them raises nothing -- it silently
truncates a persisted message.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.agent_host import AgentHostRunState
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
)
from app.modules.agent.infrastructure.harnesses.agent_host.tool_payload import (
    json_object,
)
from app.modules.usage.contracts import AgentRunUsage


@dataclass(slots=True)
class Segment:
    """Accumulated text for one stream kind, in the two halves the host has.

    The host seals its live text into an upsert before *every* non-chunk event
    and clears its buffer (``runtime.rs``, ``flush_stream_segment``). So an
    upsert is not the message so far — it is the piece streamed since the last
    upsert, and a message containing a tool call is delivered as several.

    This used to treat each one as the authoritative whole and assign it over
    everything accumulated, which is correct for exactly one upsert and lossy
    for two: an agent that said something, called a tool, then said more
    persisted only the part after the tool call. It was invisible because the
    chunks had already streamed the full text to the screen and because the
    ``startswith`` guard turned the overwrite into a silent no-delta.

    ``sealed`` is what upserts have confirmed, ``pending`` is what chunks have
    streamed since the last one, and the message is both.

    ``object_id`` is the host's identifier for the current segment, carried so
    the emitted message metadata refers to something real rather than to the
    internal stream name.
    """

    kind: str
    sealed: str = ""
    pending: str = ""
    object_id: str | None = None

    def append(self, chunk: str, object_id: str | None) -> None:
        self.pending += chunk
        if object_id is not None:
            self.object_id = object_id

    def seal(self, segment_text: str, object_id: str | None) -> str:
        """Apply an upsert, returning whatever the chunks had not streamed.

        Normally nothing: the chunks and the upsert carry the same text and the
        user has already seen it. It is non-empty when the host sealed a segment
        no chunk delivered, and the host's record wins over a disagreement,
        because a token stream cannot retract what it already emitted.
        """
        delta = (
            segment_text[len(self.pending) :]
            if segment_text.startswith(self.pending)
            else ""
        )
        self.sealed += segment_text
        self.pending = ""
        if object_id is not None:
            self.object_id = object_id
        return delta

    def take(self) -> tuple[str, str | None]:
        text, object_id = self.sealed + self.pending, self.object_id
        self.sealed, self.pending, self.object_id = "", "", None
        return text, object_id


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


def integer(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def narration_event(
    *,
    agent_run_id: UUID,
    text: str,
    object_id: str | None,
) -> AgentEvent:
    """One thing the agent said on its way to the answer.

    Marked intermediate, which is what lets a client fold it into the run's
    collapsed step list instead of showing it as the answer. The flag has been
    read by the frontend all along and written by nothing, so every one of these
    arrived looking like a final answer -- and, accumulated across a whole run
    into a single message, looking like one enormous one.
    """
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_text(
            text,
            metadata={
                "agent_host_object_id": object_id,
                "is_final_answer": False,
                "is_intermediate_assistant_message": True,
            },
        ),
        agent_run_id=agent_run_id,
    )


def usage_event(
    *,
    agent_run_id: UUID,
    model_name: str,
    payload: JsonObject,
    metadata: JsonObject,
    sequence: int,
) -> AgentEvent:
    usage = json_object(payload.get("usage")) or payload
    return AgentEvent(
        type=AgentEventType.USAGE,
        data=AgentRunUsage(
            model_name=str(usage.get("model_name") or model_name),
            input_tokens=integer(usage.get("input_tokens")),
            output_tokens=integer(usage.get("output_tokens")),
            request_count=integer(usage.get("request_count"), default=1),
            tool_call_count=integer(usage.get("tool_call_count")),
            units=number(usage.get("units")),
            metadata=metadata,
        ),
        agent_run_id=agent_run_id,
        sequence=sequence,
    )


def status_event(
    *,
    agent_run_id: UUID,
    status: str,
    payload: JsonObject,
    metadata: JsonObject,
    sequence: int,
) -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.STATUS,
        data={"status": status, "detail": payload, **metadata},
        agent_run_id=agent_run_id,
        sequence=sequence,
    )


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
