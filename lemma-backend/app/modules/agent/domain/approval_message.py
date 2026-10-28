"""What the person is told when the agent stops to ask for an approval.

A run that calls ``request_approval`` ends there, and whatever the agent wrote
last is what the person reads while the card waits. That was the lead-in to
the action -- "Now I'll send the invoice to" -- a sentence the run never
finished, and the account of what it had done and why it stopped came only in
the run after the decision, if the person made one.

So the call carries the message: ``request_approval(message=...)`` is the
agent's own words to the person -- what it has done, what it is asking to do
and why -- and it is stored as the agent's message just before the card,
whichever harness ran the call and whichever surface shows it. A call without
one still ends on a whole sentence, built from the card's own title.

Only a pause the agent raised gets one. A run that ran out of budget, and an
Agent Host's own permission prompt, are cards Lemma raised: their reason is on
the card, and the agent said nothing about them.
"""

from __future__ import annotations

from app.modules.agent.domain.pausing_tools import PausingKind
from app.modules.agent.domain.value_objects import MessageDraft, MessageKind

#: The ``request_approval`` argument holding the agent's message.
APPROVAL_MESSAGE_ARG = "message"
#: On the stored message: the approval call it was written for.
APPROVAL_MESSAGE_KEY = "approval_message_for"
#: Arguments only a card Lemma raised carries (``run_budget_pause``,
#: ``agent_host_permissions``); kept as literals so the domain does not reach
#: into either.
_RAISED_BY_LEMMA = ("run_budget_pause", "agent_host_permission")
_MESSAGE_MAX_CHARS = 4000


def approval_message_text(tool_args: object) -> str | None:
    """The message an approval call is stored with, or None when it gets none."""
    if not isinstance(tool_args, dict):
        return None
    if any(key in tool_args for key in _RAISED_BY_LEMMA):
        return None
    written = tool_args.get(APPROVAL_MESSAGE_ARG)
    if isinstance(written, str) and written.strip():
        return written.strip()[:_MESSAGE_MAX_CHARS]
    title = tool_args.get("title")
    if isinstance(title, str) and title.strip():
        return f"I need your approval before I go on: {title.strip().rstrip('.')}."
    return "I need your approval before I go on."


def with_approval_message(draft: MessageDraft) -> list[MessageDraft]:
    """An approval call, preceded by what the person is told about it."""
    if (
        draft.kind is not MessageKind.TOOL_CALL
        or draft.tool_name != PausingKind.REQUEST_APPROVAL.value
    ):
        return [draft]
    text = approval_message_text(draft.tool_args)
    if text is None:
        return [draft]
    message = MessageDraft.of_text(
        text,
        role=draft.role,
        metadata={
            # Words on the way to the card, never the run's answer.
            "is_final_answer": False,
            APPROVAL_MESSAGE_KEY: draft.tool_call_id,
        },
    )
    return [message, draft]


__all__ = [
    "APPROVAL_MESSAGE_ARG",
    "APPROVAL_MESSAGE_KEY",
    "approval_message_text",
    "with_approval_message",
]
