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

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.modules.agent.domain.run_state import ParkedSuspension as ParkedSuspension


class PausingKind(str, Enum):
    """A pausing tool, by the name its call is stored under.

    A ``str`` enum so a member is the stored name: the message log keys pauses
    by tool name, and SQL filters on it, so the two must never be spelt apart.
    """

    ASK_USER = "ask_user"
    REQUEST_APPROVAL = "request_approval"
    BROWSER_SIGN_IN = "browser_sign_in"
    WAIT_FOR = "wait_for"


WAIT_TOOL_NAME = PausingKind.WAIT_FOR.value

#: The pauses a *person* resolves. Waiting is excluded on purpose: it resolves
#: on a timer or on the thing it is watching, with nobody involved, so it must
#: never appear on an approvals list or be routed a typed reply.
#:
#: `browser_sign_in` is here for the same reason the other two are: the run ends
#: and waits, and what restarts it is a person acting. It resolves through the
#: same approvals endpoint and the same durable decision row, which is what
#: makes it reach WhatsApp, Slack, Telegram and email without any new delivery
#: path.
USER_PAUSING_KINDS = (
    PausingKind.ASK_USER,
    PausingKind.REQUEST_APPROVAL,
    PausingKind.BROWSER_SIGN_IN,
)
USER_PAUSING_TOOL_NAMES = tuple(kind.value for kind in USER_PAUSING_KINDS)

PAUSING_TOOL_NAMES = (*USER_PAUSING_TOOL_NAMES, WAIT_TOOL_NAME)

# A pausing call's `parked_in_run_id` (`MessageState`): the call does not end
# its run. A remote harness has no way to pause mid tool call, so the run stays
# going while the host's bridge waits for the answer, and that answer is
# delivered into the same run rather than by a resume. Written by Lemma when it
# records the call, never read from the model's arguments.
#
# A run's `suspended_while_parked` (`RunState`): Lemma ended this run's turn
# because a person had not answered what it was parked on within the parked
# window (`ParkedSuspension`).


@dataclass(frozen=True, slots=True)
class PendingPause:
    """A pausing tool call nobody has decided yet: what a card renders and a
    typed reply is routed to."""

    tool_call_id: str
    kind: PausingKind
    #: The call's own arguments, as stored. Unvalidated on purpose: a surface
    #: validates them against the tool's request model and must be able to tell
    #: an unparseable payload from an absent one.
    tool_args: dict[str, object]
    agent_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class PausedCall:
    """The pausing tool call a decision is about, whatever its decision state.

    A record rather than the `dict[str, object]` this used to be: the lookup
    already proves the run id is present and the kind is one of the pausing
    tools, and a dict threw both facts away one line later -- so every field had
    to be re-derived, `str()`-ed or re-checked at each use.
    """

    agent_run_id: UUID
    kind: PausingKind
    tool_args: dict[str, object]


__all__ = [
    "ParkedSuspension",
    "PausedCall",
    "PausingKind",
    "PendingPause",
    "USER_PAUSING_KINDS",
    "PAUSING_TOOL_NAMES",
    "USER_PAUSING_TOOL_NAMES",
    "WAIT_TOOL_NAME",
]
