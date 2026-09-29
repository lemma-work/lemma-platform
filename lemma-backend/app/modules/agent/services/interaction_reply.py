"""What a typed reply to a paused ``ask_user`` / ``request_approval`` means.

The agent owns the vocabulary of its own pauses: which words approve, which
deny, how a bare "2" picks the second option, what shape ``ask_user`` persisted
its questions in. A surface that gets a typed reply asks *this* whether it is an
answer and what answer it is, so the meaning of "go ahead" is one decision made
here rather than one each transport makes for itself.

What stays with the caller is plumbing: whether the message targets the pending
interaction at all, whether the person asked to type it, and falling through to
an ordinary message when this says the reply is not a decision.

Pure functions over the persisted tool args, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    JsonObject,
)
from app.modules.agent.tools.user_interaction.models import (
    AskUserQuestion,
    AskUserRequest,
)


@dataclass(frozen=True, slots=True)
class InteractionReply:
    """The decision and response a typed reply resolves a pause with."""

    decision: AgentRunApprovalDecision
    #: Empty for an approval; ``{"answers": {...}}`` for an ``ask_user``.
    response: JsonObject


# A typed approval reply is classified three ways, and the third case is the
# whole point. "approve" and "deny" are decisions; anything else is *not a
# decision at all* and has to reach the agent as what the person actually wrote.
#
# There used to be no third case: everything outside the approve set became
# DENY. Since the caller treats a classified reply as consumed, "yeah go ahead"
# cancelled the action and "wait, why do you need that?" was a denial with the
# question thrown away — neither reply was ever delivered to anyone.
#
# Ambiguity is safe here *because* there is a third case. An unmatched reply
# falls through to the normal message path, where
# ``supersede_stale_pending_interactions`` auto-denies the pending call and the
# agent receives the person's words and can ask again. So these sets stay exact
# rather than growing prefix matches, which would let "yes, but only if X" read
# as consent.
_APPROVE_ONCE_REPLIES = frozenset(
    {
        "1",
        "accept",
        "allow",
        "approve",
        "approved",
        "confirm",
        "confirmed",
        "do it",
        "do it please",
        "go",
        "go ahead",
        "go for it",
        "lgtm",
        "looks good",
        "please do",
        "sounds good",
        "k",
        "ok",
        "okay",
        "proceed",
        "run",
        "run it",
        "sure",
        "sure go ahead",
        "y",
        "yeah",
        "yep",
        "yeah go ahead",
        "yes",
        "yes do it",
        "yes go ahead",
        "yes please",
        "yup",
        "👍",
        "✅",
    }
)
_APPROVE_SESSION_REPLIES = frozenset(
    {
        "allow always",
        "allow for session",
        "always",
        "always allow",
        "approve all",
        "approve for session",
        "approve session",
        "dont ask again",
        "don't ask again",
        "don’t ask again",
        "yes to all",
    }
)
_DENY_REPLIES = frozenset(
    {
        "2",
        "abort",
        "cancel",
        "cancelled",
        "decline",
        "denied",
        "deny",
        "do not",
        "dont",
        "don't",
        "don’t",
        "n",
        "never mind",
        "nevermind",
        "no",
        "no thanks",
        "nope",
        "reject",
        "rejected",
        "stop",
        "👎",
        "❌",
    }
)


def _normalize_decision_reply(text: str) -> str:
    """Fold a typed reply to its comparable form.

    Lowercased, whitespace collapsed, and stripped of the trailing punctuation a
    person types without meaning anything by it — "Yes!" and "yes" are the same
    decision. Apostrophes are left alone so "don’t ask again" can be matched in
    both the straight and curly spellings a phone keyboard produces.
    """
    collapsed = " ".join(text.strip().lower().split())
    return collapsed.strip(".!?,;:").strip()


def classify_approval_reply(text: str) -> AgentRunApprovalDecision | None:
    """The decision this reply expresses, or ``None`` when it expresses none.

    ``None`` is not a failure. It means the person said something other than
    yes or no, and the caller must leave the approval pending and deliver the
    message instead of inventing a decision on their behalf.
    """
    normalized = _normalize_decision_reply(text)
    if not normalized:
        return None
    if normalized in _APPROVE_SESSION_REPLIES:
        return AgentRunApprovalDecision.APPROVE_FOR_SESSION
    if normalized in _APPROVE_ONCE_REPLIES:
        return AgentRunApprovalDecision.APPROVE_ONCE
    if normalized in _DENY_REPLIES:
        return AgentRunApprovalDecision.DENY
    return None


def ask_user_request_dict(tool_args: object) -> JsonObject | None:
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


def _ask_user_questions(tool_args: object) -> list[AskUserQuestion] | None:
    """The validated questions, or ``None`` when the args do not parse."""
    raw_request = ask_user_request_dict(tool_args)
    if raw_request is None:
        return None
    try:
        return AskUserRequest.model_validate(raw_request).questions
    except ValidationError:
        return None


def parse_ask_user_reply(text: str, questions: list[AskUserQuestion]) -> JsonObject:
    """Map a typed reply to an ask_user answers dict.

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


def reply_plainly_answers(*, is_approval: bool, tool_args: object, text: str) -> bool:
    """Is this text unmistakably the answer, rather than a new request?

    A person with buttons in front of them may still type "approve", or "2", or
    the option's own words — that is answering, and it would be perverse to
    treat it as a new instruction. So an exact match is still taken as one,
    whatever the platform can render.

    Deliberately narrow. ``parse_ask_user_reply`` falls back to the raw text as a
    free-form answer, which would call *every* message an answer — the thing
    being fixed. Only a recognised option, index or decision counts here.

    An approval asks ``classify_approval_reply`` itself rather than keeping a
    second, shorter word list. Two vocabularies meant the 52 phrases the
    classifier knew and the gate did not — "go ahead", "sure", "proceed",
    "lgtm", 👍 — were reported as no answer at all, and the turn the caller then
    starts supersedes the pause with an auto-DENY. The person typed "go ahead"
    and the action was cancelled.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if is_approval:
        return classify_approval_reply(stripped) is not None
    questions = _ask_user_questions(tool_args)
    if questions is None or len(questions) != 1:
        return False
    options = getattr(questions[0], "options", None) or []
    if stripped.isdigit():
        return 0 <= int(stripped) - 1 < len(options)
    return any(
        (getattr(option, "label", "") or "").lower() == stripped.lower()
        for option in options
    )


def interpret_reply(
    *, is_approval: bool, tool_args: object, text: str
) -> InteractionReply | None:
    """The decision and response this reply resolves the pause with, or None.

    ``None`` means the reply expresses no decision, which only an approval can
    say: an ask_user takes free text, so once the caller has established the
    message is an answer there is always something to record.
    """
    if not is_approval:
        # Leave the questions empty when the args do not parse: the reply is
        # then treated as free text rather than matched against options. Losing
        # the option match is better than losing the answer.
        questions = _ask_user_questions(tool_args) or []
        return InteractionReply(
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": parse_ask_user_reply(text, questions)},
        )
    decision = classify_approval_reply(text)
    return None if decision is None else InteractionReply(decision, {})
