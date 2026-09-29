"""Reading a typed reply to a paused ``ask_user`` / ``request_approval``.

The agent owns what "go ahead", "2" or an option's own label mean; a surface
that receives a typed reply asks here rather than keeping a word list of its own,
so the web, the CLI and every chat platform agree.

A submodule rather than `contracts/__init__`, like its siblings.
"""

from __future__ import annotations

from app.modules.agent.contracts.conversations_for_surfaces import PendingInteraction
from app.modules.agent.services.interaction_reply import (
    InteractionReply,
    ask_user_request_dict,
    classify_approval_reply,
    interpret_reply,
    reply_plainly_answers,
)


def reply_plainly_answers_pending(pending: PendingInteraction, text: str) -> bool:
    """Is this text unmistakably the answer to ``pending``, not a new request?"""
    return reply_plainly_answers(
        is_approval=pending.is_approval, tool_args=pending.tool_args, text=text
    )


def interpret_reply_to_pending(
    pending: PendingInteraction, text: str
) -> InteractionReply | None:
    """The decision and response ``text`` resolves ``pending`` with, or ``None``.

    ``None`` is an approval reply that expresses no decision; the caller leaves
    the pause alone and delivers the message as an ordinary one.
    """
    return interpret_reply(
        is_approval=pending.is_approval, tool_args=pending.tool_args, text=text
    )


__all__ = [
    "InteractionReply",
    "ask_user_request_dict",
    "classify_approval_reply",
    "interpret_reply_to_pending",
    "reply_plainly_answers_pending",
]
