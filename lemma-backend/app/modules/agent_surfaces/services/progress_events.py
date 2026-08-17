"""Reading agent-run events: what is progress, what is the answer, what is noise.

Split out of :mod:`progress_observer` because these are pure predicates and
extractors over an ``AgentEvent`` — no I/O, no state — while the observer itself
is all lifecycle and delivery.
"""

from __future__ import annotations

from app.modules.agent.contracts import (
    AgentEvent,
    AgentEventType,
    Conversation,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent_surfaces.platforms.rendering import (
    sanitize_user_visible_text,
    strip_thinking_tokens,
)

from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)

_MAX_PROGRESS_TEXT_LENGTH = 120
_EMAIL_REPLY_TOOL_NAMES = {
    caps.reply_tool for caps in PLATFORM_CAPABILITIES.values() if caps.reply_tool
}


def _surface_platform(conversation: Conversation) -> str | None:
    metadata = conversation.metadata or {}
    platform = metadata.get("surface_platform") if isinstance(metadata, dict) else None
    return str(platform).upper() if platform else None


def _safe_run_error_text(event: AgentEvent) -> str:
    del event
    return "I couldn’t finish that request. Try it again without resending your message."


def _email_reply_tool_called(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    return (
        isinstance(data, MessageDraft)
        and data.kind == MessageKind.TOOL_CALL
        and data.tool_name in _EMAIL_REPLY_TOOL_NAMES
    )


def _progress_text_from_event(event: AgentEvent) -> str | None:
    """Derive a short progress status from thinking/tool activity.

    Tool calls prefer an explicit ``comment`` in the tool args, falling back to
    the tool name; thinking events surface a generic "Thinking…" status.
    """
    if event.type != AgentEventType.MESSAGE:
        return None
    data = event.data
    if not isinstance(data, MessageDraft):
        return None
    if data.kind == MessageKind.TOOL_CALL:
        comment = _find_comment(data.tool_args)
        if comment:
            # A comment that is entirely reasoning sanitizes to empty -> no
            # progress update (rather than streaming a blank/leaky message).
            return _sanitize_progress_text(comment) or None
        if data.tool_name:
            return _sanitize_progress_text(f"Using {data.tool_name}")
        return None
    if data.kind == MessageKind.THINKING:
        return "Thinking…"
    return None


def _is_final_answer_event(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    if not isinstance(data, MessageDraft):
        return False
    metadata = data.metadata or {}
    return metadata.get("is_final_answer") is True


def _is_agent_host_permission_event(event: AgentEvent) -> bool:
    """An Agent Host permission pause, carried as STATUS rather than WAITING."""
    return (
        event.type == AgentEventType.STATUS
        and isinstance(event.data, dict)
        and event.data.get("status") == "permission_request"
        and bool(event.data.get("tool_call_id"))
    )


def _is_tool_activity_event(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    return isinstance(data, MessageDraft) and data.kind in (
        MessageKind.TOOL_CALL,
        MessageKind.TOOL_RETURN,
    )


def _join_text(existing: str | None, new: str) -> str:
    if not existing:
        return new
    return f"{existing}\n\n{new}"


def _assistant_text_from_event(event: AgentEvent) -> str | None:
    if event.type != AgentEventType.MESSAGE:
        return None
    data = event.data
    if not isinstance(data, MessageDraft):
        return None
    role = data.role.value if isinstance(data.role, MessageRole) else str(data.role)
    if role != MessageRole.ASSISTANT.value:
        return None
    if data.kind != MessageKind.TEXT:
        return None
    # Strip inline reasoning/thinking tags (e.g. ``…``) that some
    # OpenAI-compatible models emit inside the text content. Without this the
    # thinking block would be buffered as assistant text and delivered to the
    # surface as a normal message.
    text = strip_thinking_tokens(data.text or "")
    return text or None


def _assistant_text_was_all_reasoning(event: AgentEvent) -> bool:
    """True when the model's answer was reasoning and nothing else.

    ``_assistant_text_from_event`` returns None both for "this event carries no
    answer" and for "it carried one, and stripping the reasoning left nothing".
    Only the second is a lost answer: the model wrote `<think>` and stopped
    there, so the turn ends with the surface holding an empty string. Telling
    those apart is what lets delivery say something rather than nothing.
    """
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    if not isinstance(data, MessageDraft):
        return False
    role = data.role.value if isinstance(data.role, MessageRole) else str(data.role)
    if role != MessageRole.ASSISTANT.value or data.kind != MessageKind.TEXT:
        return False
    raw = (data.text or "").strip()
    return bool(raw) and not strip_thinking_tokens(raw)


def _find_comment(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("comment", "progress_comment", "progress", "status"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    request = value.get("request")
    if isinstance(request, dict):
        return _find_comment(request)
    return None


def _sanitize_progress_text(value: str) -> str:
    # Strip model reasoning BEFORE collapsing/truncating: a model that writes
    # ``<think>…</think>`` into a tool-call comment must never have it streamed
    # to the surface as a live progress update.
    text = " ".join(sanitize_user_visible_text(value).split())
    if len(text) <= _MAX_PROGRESS_TEXT_LENGTH:
        return text
    return text[: _MAX_PROGRESS_TEXT_LENGTH - 1].rstrip() + "..."
