"""The agent's words a chat surface hands on, and how it reads a typed reply.

What "go ahead", "2" or an option's own label mean to a paused question; which
argument of a tool call is its progress line and how ``write_todos`` renders
its checklist; how a sign-in is asked for; the note that frames other people's
channel messages as background. They are the agent's to write, so the web,
the CLI and every platform say the same; a surface reads them here. Pure: all
of it is ``domain``.
"""

from __future__ import annotations

from app.modules.agent.contracts.channels import PendingInteraction
from app.modules.agent.domain.interaction_reply import (
    InteractionReply,
    ask_user_request_dict,
    classify_approval_reply,
    interpret_reply,
    reply_plainly_answers,
)
from app.modules.agent.domain.progress_tools import (
    TODO_TOOL_NAME,
    TodoStep,
    progress_comment_for_tool_call,
    todo_plan_from_tool_return,
)
from app.modules.agent.domain.prompt.surface import background_channel_context_note
from app.modules.agent.domain.sign_in_request import (
    SignInRequest,
    sign_in_link,
    sign_in_message,
    sign_in_request_from_tool_args,
)


def reply_plainly_answers_pending(pending: PendingInteraction, text: str) -> bool:
    """Is this text unmistakably the answer to ``pending``, not a new request?"""
    return reply_plainly_answers(
        is_approval=pending.is_approval, tool_args=pending.tool_args, text=text
    )


def interpret_reply_to_pending(
    pending: PendingInteraction, text: str
) -> InteractionReply | None:
    """The decision and response ``text`` resolves ``pending`` with, or ``None``
    for an approval reply that expresses no decision."""
    return interpret_reply(
        is_approval=pending.is_approval, tool_args=pending.tool_args, text=text
    )


__all__ = [
    "InteractionReply",
    "SignInRequest",
    "TODO_TOOL_NAME",
    "TodoStep",
    "ask_user_request_dict",
    "background_channel_context_note",
    "classify_approval_reply",
    "interpret_reply_to_pending",
    "progress_comment_for_tool_call",
    "reply_plainly_answers_pending",
    "sign_in_link",
    "sign_in_message",
    "sign_in_request_from_tool_args",
    "todo_plan_from_tool_return",
]
