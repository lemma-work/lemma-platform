"""Tools that end their run by pausing rather than returning.

Each persists its tool call and is resolved later by synthesizing that call's
return and starting a fresh run that replays it. ask_user/request_approval
resolve through the approvals endpoint; wait_for resolves when whatever it is
waiting on resolves, with no person involved — but the resume is the same, which
is why they share a list.

This lives in the domain because two layers depend on the same fact for opposite
reasons: the services layer treats "a call from this list with no return" as the
marker that a conversation is waiting on a human, and history reconstruction has
to recognise the same shape so it does not mistake a pending question for an
interrupted tool and report it to the model as failed.

Membership is by tool *name*, which is why waiting is its own tool rather than an
action on ``manage_process``. A tool in this list has every call treated as a
possible pause, so a genuinely interrupted ``manage_process(action='kill')``
would be silently dropped from history instead of reported as interrupted.
"""

from __future__ import annotations

WAIT_TOOL_NAME = "wait_for"

#: The pauses a *person* resolves. Waiting is excluded on purpose: it resolves
#: on a timer or on the thing it is watching, with nobody involved, so it must
#: never appear on an approvals list or be routed a typed reply.
#:
#: `browser_sign_in` is here for the same reason the other two are: the run ends
#: and waits, and what restarts it is a person acting. It resolves through the
#: same approvals endpoint and the same durable decision row, which is what
#: makes it reach WhatsApp, Slack, Telegram and email without any new delivery
#: path.
USER_PAUSING_TOOL_NAMES = ("ask_user", "request_approval", "browser_sign_in")

PAUSING_TOOL_NAMES = (*USER_PAUSING_TOOL_NAMES, WAIT_TOOL_NAME)

__all__ = ["PAUSING_TOOL_NAMES", "USER_PAUSING_TOOL_NAMES", "WAIT_TOOL_NAME"]
