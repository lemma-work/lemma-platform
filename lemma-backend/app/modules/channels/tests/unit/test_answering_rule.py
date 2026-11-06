"""Which agents answer a line: one rule, wherever it was typed.

The same words get the same agents whether a member typed them in Lemma or on
the platform a group mirrors. On a platform the bot the line came through
answers it at its door, so the agents started here are the others.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.modules.channels.domain.answering import (
    answering_agents,
    answers_every_line,
)
from app.modules.channels.domain.entities import (
    AgentAnswers,
    ChannelKind,
    Member,
    SenderKind,
)

CHANNEL = uuid4()
POD_AGENT = uuid4()
BOT = uuid4()
EVERY = uuid4()
NAMED = uuid4()


def _seat(agent_id: UUID, answers: AgentAnswers) -> Member:
    return Member(channel_id=CHANNEL, agent_id=agent_id, answers=answers)


SEATED = (
    _seat(EVERY, AgentAnswers.EVERY_LINE),
    _seat(BOT, AgentAnswers.MENTIONED),
)


def _answering(**overrides):
    rule = {
        "sender_kind": SenderKind.MEMBER,
        "members_only": False,
        "named": (),
        "seated": SEATED,
        "channel_kind": ChannelKind.GROUP,
        "pod_agent_id": POD_AGENT,
    }
    return answering_agents(**(rule | overrides))


def test_a_group_line_gets_the_same_agents_typed_in_lemma_or_on_the_platform():
    typed_in_lemma = _answering()
    heard_through_the_bot = _answering(door_agent_id=BOT)
    assert typed_in_lemma == heard_through_the_bot == [EVERY]


def test_a_line_naming_the_bot_is_answered_by_it_here_and_at_its_door_there():
    # In Lemma it is started here; on the platform the bot answers it at its
    # door, so it is not started twice.
    assert _answering(named=[BOT]) == [BOT, EVERY]
    assert _answering(named=[BOT], door_agent_id=BOT) == [EVERY]


def test_an_agent_answering_every_line_is_left_to_its_door_when_it_is_the_bot():
    seated = (_seat(BOT, AgentAnswers.EVERY_LINE),)
    assert _answering(seated=seated) == [BOT]
    assert _answering(seated=seated, door_agent_id=BOT) == []
    assert answers_every_line(BOT, seated)
    assert not answers_every_line(EVERY, seated)


def test_another_agent_named_on_the_platform_is_started_here():
    assert _answering(named=[NAMED], door_agent_id=BOT) == [NAMED, EVERY]


def test_a_members_own_channel_falls_back_to_the_pods_agent_only_in_lemma():
    """A member's own channel only hears a line put to a door: the door's
    agent is the one it names, and the pod's agent is not added beside it."""
    direct = {"channel_kind": ChannelKind.DIRECT, "seated": ()}
    assert _answering(**direct) == [POD_AGENT]
    assert _answering(**direct, door_agent_id=BOT) == []


def test_a_note_is_answered_only_by_the_agents_it_names():
    assert _answering(members_only=True) == []
    assert _answering(members_only=True, named=[NAMED]) == [NAMED]


def test_somebody_outside_the_pod_starts_nobody_here():
    for kind in (SenderKind.CONTACT, SenderKind.AGENT):
        assert _answering(sender_kind=kind, named=[NAMED]) == []


def test_a_heard_line_whose_door_agent_is_unknown_starts_nobody_here():
    # The door answers it if anyone does: starting the agent that answers
    # every line, or the pod's agent in a member's own channel, would answer
    # the one line twice.
    assert _answering(heard=True) == []
    assert _answering(heard=True, named=(NAMED,)) == []
    assert _answering(heard=True, channel_kind=ChannelKind.DIRECT, seated=()) == []


def test_a_heard_line_with_its_door_agent_named_starts_the_others():
    assert _answering(heard=True, door_agent_id=BOT) == [EVERY]
