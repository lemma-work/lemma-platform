"""Reading agent-run events: what is progress, what is the answer, what is noise.

Split out of :mod:`progress_observer` because these are pure predicates and
extractors over an ``AgentEvent`` — no I/O, no state — while the observer itself
is all lifecycle and delivery.
"""

from __future__ import annotations

from app.modules.agent.contracts import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.contracts.progress_tools import progress_comment_for_tool_call
from app.modules.agent_surfaces.domain.display_redaction import redact_secrets_in_text
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.platforms.rendering import (
    sanitize_user_visible_text,
    strip_thinking_tokens,
)


_MAX_PROGRESS_TEXT_LENGTH = 120


def surface_platform_of(ctx: object) -> str | None:
    """The platform a run talks over, as its context names it; None for none."""
    platform = getattr(ctx, "surface_platform", None)
    return str(platform).upper() if platform else None


_MAX_FAILURE_REASON_CHARS = 600


def failure_notice(reason: str | None, platform: str | None) -> str:
    """What the person is told when their request failed, and what to do next.

    The reason is the run's own sentence for it -- a quota spent, a provider
    down, a local model server not running -- which the web transcript always
    had and a surface never did. Masked and clipped, because a provider's
    words can quote a request. Unknown failures keep the plain apology.

    The next step names only what this person can do here: a "Try again"
    control where the platform renders one, and a typed "retry" everywhere,
    since that works on email too.
    """
    clean = " ".join(
        redact_secrets_in_text(sanitize_user_visible_text(reason or "")).split()
    )
    if len(clean) > _MAX_FAILURE_REASON_CHARS:
        clean = clean[: _MAX_FAILURE_REASON_CHARS - 1].rstrip() + "…"
    capabilities = PLATFORM_CAPABILITIES.get(platform or "")
    next_step = (
        "Tap Try again, or reply “retry”, and I’ll run it again."
        if capabilities is not None and capabilities.supports_native_retry
        else "Reply “retry” and I’ll run it again."
    )
    return f"{clean or 'I couldn’t finish that request.'}\n\n{next_step}"


def _safe_run_error_text(event: AgentEvent, platform: str | None) -> str:
    """The failure notice for a run that ended on an ERROR or REJECTED event.

    An ERROR event's data is the harness's user-facing sentence (the same text
    the run row and the web transcript carry); a REJECTED one holds it under
    ``detail``. Anything else is not a sentence written for a person.
    """
    data = event.data
    if isinstance(data, dict):
        data = data.get("detail")
    return failure_notice(data if isinstance(data, str) else None, platform)


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
        comment = progress_comment_for_tool_call(data.tool_args)
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
    return data.state.is_final_answer


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
    if data.role is not MessageRole.ASSISTANT:
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
    if data.role is not MessageRole.ASSISTANT or data.kind is not MessageKind.TEXT:
        return False
    raw = (data.text or "").strip()
    return bool(raw) and not strip_thinking_tokens(raw)


def _sanitize_progress_text(value: str) -> str:
    # Strip model reasoning BEFORE collapsing/truncating: a model that writes
    # ``<think>…</think>`` into a tool-call comment must never have it streamed
    # to the surface as a live progress update.
    # A model-written status line can quote the command it is about to run;
    # mask credentials before the line is cut, so none is split by the cut.
    text = " ".join(redact_secrets_in_text(sanitize_user_visible_text(value)).split())
    if len(text) <= _MAX_PROGRESS_TEXT_LENGTH:
        return text
    return text[: _MAX_PROGRESS_TEXT_LENGTH - 1].rstrip() + "..."
