"""Resuming a paused ``ask_user`` / ``request_approval`` from a typed reply.

Lifted out of ``ingress_service`` unchanged: it never touched instance state,
and the ingress service is far past the size where a self-contained 70-line
branch should still be living inside it.

Best-effort by construction — every failure returns False and the message falls
through to the normal new-message path, because misreading a reply as an answer
is worse than treating an answer as a new message.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.user_interaction.models import AskUserRequest
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext

logger = get_logger(__name__)


def _ask_user_request_dict(tool_args: object) -> dict[str, Any] | None:
    """The ``AskUserRequest`` payload from a persisted ask_user call's args.

    pydantic-ai flattens a tool's single pydantic-model parameter, so a real
    ``ask_user(ctx, request: AskUserRequest)`` call persists its args as the
    model's own fields — ``{"questions": [...]}`` — NOT ``{"request": {...}}``.
    Older/hand-built (e.g. scripted-test) calls may still use the wrapped shape.
    Accept both so the questions are never lost (which silently swallows the
    whole ask_user — no card, no text fallback, run stuck WAITING).
    """
    if not isinstance(tool_args, dict):
        return None
    request = tool_args.get("request")
    if isinstance(request, dict):
        return request
    if isinstance(tool_args.get("questions"), list):
        return tool_args
    return None


def _ask_user_question_headers(tool_args: object) -> list[str]:
    """Extract question headers from a persisted ask_user tool call's args."""
    request = _ask_user_request_dict(tool_args)
    if request is None:
        return []
    questions = request.get("questions")
    if not isinstance(questions, list):
        return []
    headers: list[str] = []
    for question in questions:
        if isinstance(question, dict):
            header = question.get("header")
            if isinstance(header, str) and header:
                headers.append(header)
    return headers


def _parse_ask_user_reply(
    text: str,
    questions: list,
) -> dict[str, Any]:
    """Map a typed surface reply to an ask_user answers dict.

    Single question: tries to match the text as a 1-based number or an exact
    case-insensitive option label; falls back to the raw text (free-form Other).
    Multiple questions: maps the raw text to every header — the agent receives
    the same string for all questions, which is the best we can do with a single
    unstructured reply.
    """
    if not questions:
        return {"answer": text}
    if len(questions) == 1:
        q = questions[0]
        options = getattr(q, "options", None) or []
        stripped = text.strip()
        # Number → option by 1-based index
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(options):
                return {q.header: options[idx].label}
        # Case-insensitive label match
        lower = stripped.lower()
        for opt in options:
            if (getattr(opt, "label", "") or "").lower() == lower:
                return {q.header: opt.label}
        # Free-form Other
        return {q.header: stripped}
    # Multiple questions — crude but the only option for a plain text reply
    return {q.header: text for q in questions}


def _parse_approval_decision(text: str) -> "AgentRunApprovalDecision":
    """Parse a typed surface reply as an approval decision.

    "approve", "yes", "y", "ok", "confirm", "1", "run", "allow" → APPROVE_ONCE.
    Anything else → DENY (safe default).
    """
    from app.modules.agent.contracts import AgentRunApprovalDecision

    _APPROVE_WORDS = {
        "approve",
        "yes",
        "y",
        "ok",
        "okay",
        "confirm",
        "1",
        "run",
        "allow",
        "go",
    }
    if text.strip().lower() in _APPROVE_WORDS:
        return AgentRunApprovalDecision.APPROVE_ONCE
    return AgentRunApprovalDecision.DENY


async def maybe_resume_pending_interaction(
    context: SurfaceChatContext,
    message_text: str,
    *,
    conversation_service: ConversationService,
) -> bool:
    """Resume a paused ask_user or request_approval from a typed surface reply.

    Returns True when the message was consumed as an answer so the caller
    skips the normal new-message path. Best-effort: any failure returns False.

    ask_user: parses the reply as a numbered option (1, 2, …) or an exact
    label match, falling back to the raw text as a free-form "Other" answer.
    request_approval: "approve"/"yes"/… → APPROVE_ONCE; anything else → DENY.
    """
    if context.conversation_id is None:
        return False
    text = (message_text or "").strip()
    if not text:
        return False
    try:
        pending = await conversation_service.get_pending_user_interaction(
            conversation_id=context.conversation_id
        )
        if not isinstance(pending, dict):
            return False
        kind = str(pending.get("kind") or "")
        conversation = (
            await conversation_service.conversation_repository.get_conversation(
                context.conversation_id
            )
        )
        if conversation is None:
            return False

        if kind == "ask_user":
            raw_request = _ask_user_request_dict(pending.get("tool_args"))
            questions = []
            if raw_request is not None:
                try:
                    questions = AskUserRequest.model_validate(raw_request).questions
                except ValidationError:
                    # Leave `questions` empty: the reply is then treated as
                    # free text rather than matched against options. Losing the
                    # option match is better than losing the answer.
                    pass
            answers = _parse_ask_user_reply(text, questions)
            decision = AgentRunApprovalDecision.APPROVE_ONCE
            response: dict[str, Any] = {"answers": answers}
        else:
            decision = _parse_approval_decision(text)
            response = {}

        # Deferred: a webhook deadline is shorter than an approved command.
        await conversation_service.resolve_user_approval_internal(
            conversation=conversation,
            approval_id=str(pending.get("tool_call_id") or ""),
            user_id=context.user_id,
            pod_id=context.pod_id,
            decision=decision,
            response=response,
            defer_reconciliation=True,
        )
        return True
    except Exception:
        logger.debug(
            "agent_surfaces.ingress_service.surface_interaction_typed_reply_resume.diagnostic",
            conversation_id=context.conversation_id,
        )
        return False
