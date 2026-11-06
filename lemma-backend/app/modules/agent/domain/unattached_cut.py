"""Run-less messages past their bound: the newest kept, and the cut said.

A member answering a contact by hand for a while leaves more run-less lines
than a history carries. Cut silently, the agent taking the conversation back
answers a thread whose start it never saw; so the cut is told, in a notice in
front of the oldest line kept. Copies a contact merge folded in have their own
bound and notice (``folded_turns``).
"""

from __future__ import annotations

from app.modules.agent.domain.entities import Message, MessageKind, MessageRole
from app.modules.agent.domain.folded_turns import folded_from
from app.modules.agent.domain.history_notices import SYNTHETIC_NOTICE_PREFIX

#: Names the notice, for a reader that wants to find it.
UNATTACHED_CUT_KIND = "unattached_messages_cut"


def newest_unattached(messages: list[Message], *, keep: int) -> list[Message]:
    """``messages`` with only the newest ``keep`` that are not copies, and a
    notice in front of the oldest kept when any went."""
    own = [
        index for index, message in enumerate(messages) if folded_from(message) is None
    ]
    if len(own) <= keep:
        return messages
    left_out = set(own[: len(own) - keep])
    oldest_kept = own[len(own) - keep]
    kept: list[Message] = []
    for index, message in enumerate(messages):
        if index in left_out:
            continue
        if index == oldest_kept:
            kept.append(_cut_notice(message))
        kept.append(message)
    return kept


def _cut_notice(oldest_kept: Message) -> Message:
    """It takes the sequence of the message it precedes, which is that
    message's alone, and sits before it in the list, so the harness's stable
    sort keeps it in front."""
    return Message(
        conversation_id=oldest_kept.conversation_id,
        sequence=oldest_kept.sequence,
        agent_run_id=None,
        role=MessageRole.USER,
        kind=MessageKind.NOTIFICATION,
        text=(
            f"{SYNTHETIC_NOTICE_PREFIX} Earlier messages written outside a run "
            "in this conversation are older than what is carried here and are "
            "not shown."
        ),
        metadata={"synthetic": True, "summary_kind": UNATTACHED_CUT_KIND},
    )
