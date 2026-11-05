"""What a member's run in a group is shown of the group.

The failure this pins: a member's run was not shown what a person outside the
pod said in the group, nor the bot's answers to them, so it recorded half of
what the group had agreed -- and the next run read every hidden line anyway,
through ``channel_read``. The view now shows everything said in the group, a
stranger's lines marked as theirs, and withholds only members' notes, which
never reached the group.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.channels.contracts import (
    ChannelMessage,
    Principal,
    Sender,
)
from app.modules.channels.contracts.backgrounds import (
    BackgroundItem,
    BackgroundRefs,
    BackgroundView,
    _shown,
)

pytestmark = pytest.mark.unit

_POD = uuid4()
_CHANNEL = uuid4()
_MEMBER = uuid4()
_GUEST = uuid4()


def _line(seq: int, sender: Sender, text: str, *, members_only: bool = False):
    return ChannelMessage(
        id=uuid4(),
        channel_id=_CHANNEL,
        pod_id=_POD,
        seq=seq,
        sender=sender,
        text=text,
        members_only=members_only,
        created_at=datetime.now(timezone.utc),
    )


def _room() -> list[ChannelMessage]:
    agent = uuid4()
    return [
        _line(1, Sender.member(_MEMBER, display_name="Deepak"), "breakfast 2248"),
        _line(2, Sender.contact(_GUEST, display_name="Guest"), "we are 6 in total"),
        _line(
            3,
            Sender.agent(agent, on_behalf_of=Principal.contact(_GUEST)),
            "Noted, 6 people.",
        ),
        _line(
            4,
            Sender.member(_MEMBER, display_name="Deepak"),
            "private note",
            members_only=True,
        ),
    ]


def _member_view(lines: list[ChannelMessage]) -> BackgroundRefs:
    return BackgroundRefs(
        pod_id=_POD,
        channel_id=_CHANNEL,
        view=BackgroundView.MEMBER_IN_GROUP,
        agent_name="Lem",
        items=tuple(BackgroundItem(line_id=line.id, seq=line.seq) for line in lines),
    )


def test_a_members_run_reads_a_strangers_line_and_the_answer_to_it():
    lines = _room()

    shown = _shown(
        _member_view(lines), {line.id: line for line in lines}, members={_MEMBER}
    )

    assert [(said.text, said.outside_pod) for said in shown.lines] == [
        ("breakfast 2248", False),
        ("we are 6 in total", True),
        ("Noted, 6 people.", False),
    ]
    # Only the note members kept out of the chat is left out.
    assert shown.withheld == 1


def test_a_member_who_left_the_pod_reads_as_outside():
    lines = _room()

    shown = _shown(
        _member_view(lines), {line.id: line for line in lines}, members=set()
    )

    assert shown.lines[0].outside_pod is True
