"""Accumulating an agent's streamed text, and reading events back out.

Split from the normalizer because these are the two halves of one contract
the host also implements, in Rust: ``event_text`` mirrors ``runtime::
chunk_text``, and ``Segment`` mirrors the buffer the host seals into an
upsert. ``tests/fixtures/wire_contract.json`` holds both sides to the same
cases, because a disagreement between them raises nothing -- it silently
truncates a persisted message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.modules.agent.domain.agent_host import AgentHostRunState
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    JsonObject,
    MessageDraft,
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

    ``inserts`` is rich content -- an image the artifact writer saved as a pod
    file and rendered as markdown -- that this side added and the host never
    will. The host's ``chunk_text`` reads an image block as no text at all, so
    its upsert does not contain the markdown. Appending it to ``pending`` made
    ``pending`` stop being a prefix of the upsert, the ``startswith`` check
    failed, and the upsert's text replaced everything: the image streamed to
    the screen and was gone from the saved message. Each insert is kept apart,
    at the offset into the host's text where it arrived, and spliced back in
    when the segment is sealed or taken.
    """

    kind: str
    sealed: str = ""
    pending: str = ""
    object_id: str | None = None
    inserts: list[tuple[int, str]] = field(default_factory=list)

    def append(self, chunk: str, object_id: str | None, *, rich: str = "") -> None:
        """Take a chunk: host text, then whatever rich content rode with it."""
        self.pending += chunk
        if rich:
            self.inserts.append((len(self.pending), rich))
        if object_id is not None:
            self.object_id = object_id

    def seal(self, segment_text: str, object_id: str | None) -> str:
        """Apply an upsert, returning whatever the chunks had not streamed.

        Normally nothing: the chunks and the upsert carry the same text and the
        user has already seen it. It is non-empty when the host sealed a segment
        no chunk delivered, and the host's record wins over a disagreement,
        because a token stream cannot retract what it already emitted. Rich
        content survives either way: on a disagreement its offsets are clamped
        to the host's text, which keeps the image even if not in its exact spot.
        """
        delta = (
            segment_text[len(self.pending) :]
            if segment_text.startswith(self.pending)
            else ""
        )
        self.sealed += _splice(segment_text, self.inserts)
        self.pending = ""
        self.inserts = []
        if object_id is not None:
            self.object_id = object_id
        return delta

    def take(self) -> tuple[str, str | None]:
        text = self.sealed + _splice(self.pending, self.inserts)
        object_id = self.object_id
        self.sealed, self.pending, self.object_id = "", "", None
        self.inserts = []
        return text, object_id


def _splice(text: str, inserts: list[tuple[int, str]]) -> str:
    """``text`` with each rich insert put back at its offset, in order."""
    if not inserts:
        return text
    parts: list[str] = []
    cursor = 0
    for offset, rich in inserts:
        offset = max(cursor, min(offset, len(text)))
        parts.append(text[cursor:offset])
        parts.append(rich)
        cursor = offset
    parts.append(text[cursor:])
    return "".join(parts)


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


def thought_event(
    *,
    agent_run_id: UUID,
    text: str,
    object_id: str | None,
) -> AgentEvent:
    """One step's reasoning. Never the answer, so never final."""
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_thinking(
            text,
            metadata={"agent_host_object_id": object_id, "is_final_answer": False},
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
    """One turn's token usage, from the host's ``usage`` event.

    The host emits this once per turn, from the adapter's end-of-turn usage
    (``AgentHostUsagePayload``), so it is exactly one model request as the
    usage ledger counts them. It is the *only* event that produces usage. The
    old ``usage_update`` was ACP's context-window report, not token usage, and
    reading it as usage recorded zero tokens and one request per notification.

    ``input_tokens`` is kept as the adapter reported it, which excludes cached
    input; the cache and reasoning counts go into metadata under the keys the
    in-process harness uses, so neither is lost and neither is double-counted.
    """
    extra: JsonObject = {}
    for source, target in (
        ("cached_input_tokens", "cache_read_tokens"),
        ("reasoning_tokens", "reasoning_tokens"),
        ("total_tokens", "total_tokens"),
    ):
        if payload.get(source) is not None:
            extra[target] = integer(payload.get(source))
    return AgentEvent(
        type=AgentEventType.USAGE,
        data=AgentRunUsage(
            model_name=model_name,
            input_tokens=integer(payload.get("input_tokens")),
            output_tokens=integer(payload.get("output_tokens")),
            request_count=1,
            metadata={**metadata, **extra},
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


def no_terminal_message(state: AgentHostRunState) -> str:
    """What to say about a run whose lease ended without a terminal event.

    Deliberately the *last* resort. It describes Lemma's plumbing rather than
    what happened to the run, so it is only ever right when the lease recorded
    no reason of its own -- a run that genuinely stopped reporting. Anything
    that was refused, expired or recovered has a sentence about itself, and
    ``finish_without_terminal`` prefers it.

    Kept here beside ``expiry_message``'s siblings so the wordings a person
    reads are all in one file, and so the normalizer holds no prose.
    """
    return (
        f"Agent Host reached terminal checkpoint {state.value} "
        "without its required terminal event"
    )


def is_terminal_event(event: AgentEvent) -> bool:
    return event.type in {
        AgentEventType.COMPLETED,
        AgentEventType.STOPPED,
        AgentEventType.ERROR,
        AgentEventType.REJECTED,
        AgentEventType.WAITING,
    }
