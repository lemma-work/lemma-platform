"""Tools that end their run by pausing rather than returning.

Each persists its tool call and is resolved later by synthesizing that call's
return and starting a fresh run that replays it. ask_user/request_approval
resolve through the approvals endpoint; snooze resolves on a timer, with no
person involved — but the resume is the same, which is why they share a list.

This lives in the domain because two layers depend on the same fact for opposite
reasons: the services layer treats "a call from this list with no return" as the
marker that a conversation is waiting on a human, and history reconstruction has
to recognise the same shape so it does not mistake a pending question for an
interrupted tool and report it to the model as failed.
"""

from __future__ import annotations

SNOOZE_TOOL_NAME = "snooze"

#: The pauses a *person* resolves. A snooze is excluded on purpose: it resolves
#: on a timer with nobody involved, so it must never appear on an approvals list
#: or be routed a typed reply.
USER_PAUSING_TOOL_NAMES = ("ask_user", "request_approval")

PAUSING_TOOL_NAMES = (*USER_PAUSING_TOOL_NAMES, SNOOZE_TOOL_NAME)

__all__ = ["PAUSING_TOOL_NAMES", "SNOOZE_TOOL_NAME", "USER_PAUSING_TOOL_NAMES"]
