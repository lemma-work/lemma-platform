"""Resuming a paused ``ask_user`` / ``request_approval`` from a typed reply.

Lifted out of ``ingress_service`` unchanged: it never touched instance state,
and the ingress service is far past the size where a self-contained 70-line
branch should still be living inside it.

Falling through to the normal new-message path is the right answer when the
reply is not an answer — but it is the *wrong* answer when the reply was a
decision we then failed to record. Starting a turn supersedes the pause with an
auto-DENY, so a database hiccup while writing an "approve" silently cancelled
the action the person had just approved: the same question-became-a-cancellation
this module exists to prevent, reached by a different route and logged at debug,
which `LOG_LEVEL=INFO` drops. Hence three outcomes rather than a bool.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.user_interaction.models import AskUserRequest
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.free_text_answer import (
    forget_free_text_answer_wanted,
    free_text_answer_wanted_for,
)

logger = get_logger(__name__)


class ResumeOutcome(StrEnum):
    """What became of a typed reply offered to a paused interaction."""

    #: Resolved the pause. The caller starts no turn.
    CONSUMED = "CONSUMED"
    #: Not an answer to anything — deliver it as an ordinary message, which
    #: supersedes the pause with a denial the agent can see.
    NOT_A_DECISION = "NOT_A_DECISION"
    #: It *was* a decision and recording it failed. The caller must not start a
    #: turn: doing so auto-denies the very approval the person just granted.
    #: The pause stays, so saying so and letting them retry is recoverable.
    FAILED = "FAILED"


_APPROVAL_WORDS = {
    "approve",
    "yes",
    "y",
    "ok",
    "okay",
    "confirm",
    "run",
    "allow",
    "go",
    "deny",
    "no",
    "n",
    "reject",
    "decline",
    "cancel",
    "stop",
}


def _plainly_answers(pending: dict[str, Any], text: str) -> bool:
    """Is this text unmistakably the answer, rather than a new request?

    A person with buttons in front of them may still type "approve", or "2", or
    the option's own words — that is answering, and it would be perverse to
    treat it as a new instruction. So an exact match is still taken as one,
    whatever the platform can render.

    Deliberately narrow. `_parse_ask_user_reply` falls back to the raw text as a
    free-form answer and `_parse_approval_decision` reads anything unrecognised
    as a denial; either would call *every* message an answer, which is the thing
    being fixed. Only a recognised option, index or decision word counts here.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if str(pending.get("kind") or "") == "request_approval":
        return stripped.lower() in _APPROVAL_WORDS
    raw_request = _ask_user_request_dict(pending.get("tool_args"))
    if raw_request is None:
        return False
    try:
        questions = AskUserRequest.model_validate(raw_request).questions
    except ValidationError:
        return False
    if len(questions) != 1:
        return False
    options = getattr(questions[0], "options", None) or []
    if stripped.isdigit():
        return 0 <= int(stripped) - 1 < len(options)
    return any(
        (getattr(option, "label", "") or "").lower() == stripped.lower()
        for option in options
    )


async def _is_an_answer(
    context: SurfaceChatContext,
    *,
    conversation_service: ConversationService,
    pending: dict[str, Any],
    text: str,
    tool_call_id: str,
) -> bool:
    """Is this typed message answering the pause, or getting on with something else?

    The composer stays enabled while a conversation is WAITING, so somebody can
    type straight past a card — and until this asked, every such message was
    taken as the answer to whatever was pending, however old. A question nobody
    tapped therefore swallowed the next instruction anybody sent, recorded it as
    the answer, and started no run for it. Only surfaces behaved that way; a new
    message from the web or the CLI supersedes a stale pause and carries on (see
    `ConversationTurns.start` -> `supersede_stale_pending_interactions`).

    Three cases are genuinely an answer:

    * the words plainly answer it — an offered option, its number, "approve";
    * they asked to type it, by tapping "Other" on the card, and this is the
      pause they tapped it on;
    * the card reached them as text, so typing is the only way to answer at all.

    That last one is recorded where the text is sent rather than inferred from
    the platform, because the two differ: Slack renders buttons and still falls
    back to a formatted message when a block payload is rejected, and somebody
    looking at plain text has nothing to tap whatever the platform can do.

    Anything else is a new message, and saying so is what lets the normal path
    mark the pause unanswered, tell the agent, and run what was actually asked.
    """
    # Asked before the platform, because an unmistakable answer is one wherever
    # it was typed — and because it needs nothing but the words.
    if _plainly_answers(pending, text):
        return True
    repository = conversation_service.conversation_repository
    if await free_text_answer_wanted_for(
        repository,
        conversation_id=context.conversation_id,
        tool_call_id=tool_call_id,
    ):
        await forget_free_text_answer_wanted(
            repository, conversation_id=context.conversation_id
        )
        return True
    return False


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


def _classify_approval_reply(text: str) -> "AgentRunApprovalDecision | None":
    """The decision this reply expresses, or ``None`` when it expresses none.

    ``None`` is not a failure. It means the person said something other than
    yes or no, and the caller must leave the approval pending and deliver the
    message instead of inventing a decision on their behalf.
    """
    from app.modules.agent.contracts import AgentRunApprovalDecision

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


async def maybe_resume_pending_interaction(
    context: SurfaceChatContext,
    message_text: str,
    *,
    conversation_service: ConversationService,
) -> ResumeOutcome:
    """Resume a paused ask_user or request_approval from a typed surface reply.

    ``_is_an_answer`` decides first whether this message is answering the pause
    at all — words that plainly answer it, an "Other" they tapped on this call,
    or a card that reached them as text with nothing to tap. Anything else is a
    new message and falls through, so the normal path can mark the pause
    unanswered and run what was actually asked.

    Given that it *is* an answer: ask_user takes a numbered option, an exact
    label, or free text. request_approval takes only a reply expressing a
    decision — "approve"/"yes"/… → APPROVE_ONCE, "approve session"/… →
    APPROVE_FOR_SESSION, "deny"/"no"/… → DENY. Anything else is
    ``NOT_A_DECISION``, because an approval has no free-form answer and guessing
    one on the person's behalf is how a question became a cancellation.

    Failing to *look up* the pause is ``NOT_A_DECISION``: we never learned there
    was one, and the turn the caller then starts would fail on the same broken
    session anyway. Failing to *record* a decision we had already classified is
    ``FAILED``, and the difference matters — that is the only path where
    falling through would deny an approval the person granted.
    """
    if context.conversation_id is None:
        return ResumeOutcome.NOT_A_DECISION
    text = (message_text or "").strip()
    if not text:
        return ResumeOutcome.NOT_A_DECISION
    # Set the moment the decision is settled and the only thing left is the
    # write. One handler rather than two, so the module's broad-catch count does
    # not grow, and so there is exactly one place that decides which it was.
    recording = False
    try:
        pending = await conversation_service.get_pending_user_interaction(
            conversation_id=context.conversation_id
        )
        if not isinstance(pending, dict):
            return ResumeOutcome.NOT_A_DECISION
        if not await _is_an_answer(
            context,
            conversation_service=conversation_service,
            pending=pending,
            text=text,
            tool_call_id=str(pending.get("tool_call_id") or ""),
        ):
            # A new message, not an answer. Falling through is the whole fix:
            # `add_user_message_and_start_run` supersedes the unanswered pause,
            # writes the tool return that tells the agent it was never answered
            # (an approval always as a denial, never an approval), and runs what
            # the person actually asked for.
            return ResumeOutcome.NOT_A_DECISION
        kind = str(pending.get("kind") or "")
        conversation = (
            await conversation_service.conversation_repository.get_conversation(
                context.conversation_id
            )
        )
        if conversation is None:
            return ResumeOutcome.NOT_A_DECISION

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
            classified = _classify_approval_reply(text)
            if classified is None:
                # Not a decision — a question, a correction, a change of plan.
                # Leave the approval pending and let the caller deliver this as
                # an ordinary message: starting a turn supersedes the pause with
                # an explicit denial the agent can see, and the person's actual
                # words arrive alongside it. Consuming this as a decision is how
                # both the words and the question used to be lost.
                return ResumeOutcome.NOT_A_DECISION
            decision = classified
            response = {}

        recording = True
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
        return ResumeOutcome.CONSUMED
    except Exception:
        if recording:
            # The person decided and we could not write it down. Never fall
            # through: the turn that would start supersedes this pause with an
            # auto-DENY, turning their "approve" into a cancellation.
            logger.error(
                "agent_surfaces.ingress_service.typed_reply_decision_not_recorded.failed",
                conversation_id=context.conversation_id,
                exc_info=True,
            )
            return ResumeOutcome.FAILED
        logger.warning(
            "agent_surfaces.ingress_service.typed_reply_lookup_failed.degraded",
            conversation_id=context.conversation_id,
            exc_info=True,
        )
        return ResumeOutcome.NOT_A_DECISION
