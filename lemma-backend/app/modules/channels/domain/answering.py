"""Which of the pod's agents answer a line: one rule, wherever it was said.

A line is a member's words in a channel, whether they typed it in Lemma or on
the platform a group mirrors. The agents that answer it are the same either
way: those it names, those in the channel that answer every line, and -- in a
member's own channel, where it names none -- the pod's agent.

A line heard on a platform came through a door: the bot one agent is reached
by there. That agent answers it at the door, whenever the platform put the
line to it -- a direct message, an @mention, a reply in its thread, its name
-- or the agent answers every line in the channel; ``agent_surfaces`` decides
which and starts its turn, with the platform's typing, files and cards. The
other agents the rule names are started for the line here, exactly as for a
line typed in Lemma, and answer at its place too. A member's own channel only
ever hears a line put to a door, so the door's agent is the one it names.

A members' note is answered only by the agents it names, as a note: notes are
members talking, never a request to the agents answering every line. A
contact's line starts nobody here: who answers somebody outside the pod is
the door's to decide, from what the pod made Public, never an agent that
happens to sit in the channel.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from app.modules.channels.domain.entities import (
    AgentAnswers,
    ChannelKind,
    Member,
    SenderKind,
)


def answering_agents(
    *,
    sender_kind: SenderKind,
    members_only: bool,
    named: Sequence[UUID],
    seated: Iterable[Member],
    channel_kind: ChannelKind,
    pod_agent_id: UUID | None,
    door_agent_id: UUID | None = None,
    heard: bool = False,
) -> list[UUID]:
    """The agents started for a line, in the order they are named.

    ``named``: the agents the line names in its words. ``door_agent_id``: for
    a line heard on a platform (``heard``), the agent of the bot it came
    through, which answers it there and is never started here. A heard line
    whose door's agent cannot be named starts nobody here: the door answers
    it if anyone does, and starting its agent again would answer it twice.
    """
    if sender_kind is not SenderKind.MEMBER:
        return []
    if heard and door_agent_id is None:
        return []
    answering = list(dict.fromkeys(named))
    if not members_only:
        answering.extend(
            seat.agent_id
            for seat in seated
            if seat.answers is AgentAnswers.EVERY_LINE
            and seat.agent_id is not None
            and seat.agent_id not in answering
        )
    if (
        not answering
        and door_agent_id is None
        and channel_kind is ChannelKind.DIRECT
        and pod_agent_id
    ):
        answering.append(pod_agent_id)
    return [agent_id for agent_id in answering if agent_id != door_agent_id]


def answers_every_line(agent_id: UUID, seated: Iterable[Member]) -> bool:
    """Whether this agent answers every line said in the channel -- on a
    platform too, where nobody has to put it to its bot."""
    return any(
        seat.agent_id == agent_id and seat.answers is AgentAnswers.EVERY_LINE
        for seat in seated
    )
