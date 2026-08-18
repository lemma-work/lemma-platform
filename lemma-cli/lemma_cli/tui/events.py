"""Typed chat events normalized from backend SSE stream payloads.

Mirrors the parsing proven in cli_core.chat.ChatRenderer, but yields data
objects instead of printing, so screens can render them as widgets. Also used
to replay persisted MessageResponse payloads when resuming a conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..cli_core.chat import StreamEvent


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class TextMessage:
    text: str
    role: str = "assistant"


@dataclass(frozen=True)
class Thinking:
    text: str


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    tool_call_id: str | None
    tool_input: Any


@dataclass(frozen=True)
class ToolReturn:
    tool_name: str
    tool_call_id: str | None
    result: Any


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0


@dataclass(frozen=True)
class Status:
    text: str


@dataclass(frozen=True)
class ErrorEvent:
    text: str


@dataclass(frozen=True)
class Terminal:
    kind: str  # "completed" | "stopped"
    detail: str = ""


ChatEvent = (
    Token
    | TextMessage
    | Thinking
    | ToolCall
    | ToolReturn
    | Usage
    | Status
    | ErrorEvent
    | Terminal
)

TERMINAL_TYPES = {"completed", "stopped"}


def normalize_event(event: StreamEvent) -> ChatEvent | None:
    """Translate a raw StreamEvent into a typed ChatEvent (None = ignore)."""
    event_type = event.type.lower()
    data = event.data
    if event_type == "token":
        # `kind` rides beside `data` on the wire ("text", "thinking", "tool").
        # Anything but the answer channel would otherwise be typed into the
        # transcript as if the assistant had said it.
        if event.kind is not None and event.kind != "text":
            return None
        text = str(data or "")
        return Token(text) if text else None
    if event_type == "message":
        return _normalize_message(data)
    if event_type == "usage":
        if not isinstance(data, dict):
            return None
        return Usage(
            input_tokens=int(data.get("input_tokens") or 0),
            output_tokens=int(data.get("output_tokens") or 0),
            tool_call_count=int(data.get("tool_call_count") or 0),
        )
    if event_type == "error":
        return ErrorEvent(_status_text(data) or str(data))
    if event_type in TERMINAL_TYPES:
        return Terminal(kind=event_type, detail=_status_text(data) or "")
    text = _status_text(data)
    return Status(text) if text else None


def _normalize_message(data: Any) -> ChatEvent | None:
    if not isinstance(data, dict):
        text = str(data or "")
        return TextMessage(text) if text else None

    role = str(data.get("role") or "assistant").lower()
    raw_kind = data.get("kind")
    if raw_kind is None:
        # Defensive: a plain text body emitted without an explicit kind.
        body = data.get("text")
        if body is None:
            body = data.get("content")
        text = body if isinstance(body, str) else ("" if body is None else str(body))
        return TextMessage(text, role=role) if text else None

    kind = str(raw_kind).lower()
    tool_name = str(data.get("tool_name") or "tool")
    tool_call_id = data.get("tool_call_id")
    tool_call_id = str(tool_call_id) if tool_call_id else None

    if kind == "text":
        text = str(data.get("text") or "")
        return TextMessage(text, role=role) if text else None
    if kind == "thinking":
        return Thinking(str(data.get("text") or ""))
    if kind == "tool_call":
        return ToolCall(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=data.get("tool_args"),
        )
    if kind == "tool_return":
        return ToolReturn(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            result=data.get("tool_result"),
        )
    if kind == "notification":
        text = _status_text(data.get("text")) or str(data.get("text") or "")
        return Status(text) if text else None
    text = _status_text(data.get("metadata"))
    return Status(text) if text else None


def normalize_history_message(message: dict[str, Any]) -> ChatEvent | None:
    """Normalize a persisted MessageResponse dict for conversation replay."""
    return _normalize_message(message)


def _status_text(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("status", "error", "message"):
            if data.get(key):
                return str(data[key])
    return None
