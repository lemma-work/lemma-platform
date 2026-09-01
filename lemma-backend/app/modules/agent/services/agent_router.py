"""Which agent, if any, answers a message nobody addressed.

Two stages, and the order is the design. An explicit ``@mention`` -- or a
message inside a subthread an agent is already answering -- is settled here
without a model ever seeing it. Only what is left over reaches one, and the
answer it is asked for is a name, never a reply.

Silence is the expected outcome. In a conversation where people mostly talk to
each other, most messages are for nobody, and a model asked "who should reply?"
will find somebody to be helpful with. The prompt says so, the roster is the
only thing it may choose from, and being wrong costs one skipped reply rather
than a fabricated one.

See ``docs/design/agent-conversations.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RosterAgent:
    """One agent present in a conversation, as the router sees it."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """What is known about a message before anybody has decided who it is for."""

    text: str
    #: None when a person wrote it. Set when an agent did, which is what stops
    #: the room from talking to itself.
    author_agent_id: UUID | None = None
    #: Resolved by the surface or the composer, not parsed here: the platforms
    #: encode mentions differently and one of them already knows which.
    mentioned_agent_id: UUID | None = None
    #: The agent already answering the subthread this landed in, if any. A
    #: reply inside an agent's thread is addressed to it -- mention once, then
    #: talk normally.
    subthread_agent_id: UUID | None = None


def addressed_agent(message: InboundMessage, roster: list[RosterAgent]) -> UUID | None:
    """Stage one: who this is explicitly for, without asking anybody.

    A mention for an agent that is not in the conversation is not an address.
    Resolving it anyway would let a name typed in a room reach an agent that
    was never added to it.
    """
    present = {agent.id for agent in roster}
    for candidate in (message.mentioned_agent_id, message.subthread_agent_id):
        if candidate is not None and candidate in present:
            return candidate
    return None


def routing_is_needed(message: InboundMessage, roster: list[RosterAgent]) -> bool:
    """Whether stage two should run at all.

    Three structural answers, none of which needs a model:

    - **An agent wrote it.** Otherwise agent A posts, the router sees a new
      message, routes to agent B, who posts, and it never stops. A per-turn
      budget is a backstop; not considering agent authorship is the fix.
    - **Nobody is there.** No candidates, no question.
    - **Exactly one agent is there.** That is the default conversation and
      almost all of them: one candidate means every message is addressed, and
      paying for a model to say so on every message would be the whole cost of
      this feature for none of its value.
    """
    if message.author_agent_id is not None:
        return False
    if len(roster) <= 1:
        return False
    return addressed_agent(message, roster) is None


def sole_agent(roster: list[RosterAgent]) -> UUID | None:
    """The one agent present, when there is exactly one."""
    return roster[0].id if len(roster) == 1 else None


def resolve_router_choice(name: str | None, roster: list[RosterAgent]) -> UUID | None:
    """Turn what the model said into an agent, or into silence.

    Anything unrecognised is silence. A model that answers with a name nobody
    has, with prose, or with an apology has not chosen an agent, and the
    failure mode worth avoiding is inventing one from a near-match.
    """
    if not name:
        return None
    wanted = name.strip().lstrip("@").casefold()
    if wanted in {"", "none", "nobody", "no one", "null"}:
        return None
    for agent in roster:
        if agent.name.casefold() == wanted:
            return agent.id
    return None


def resolve_router_choices(
    names: list[str] | None, roster: list[RosterAgent]
) -> list[UUID]:
    """Turn what the model said into agents, dropping anything unrecognised.

    Order is kept: the model is asked to answer most-relevant first, and a
    caller that can only dispatch one takes the head.
    """
    chosen: list[UUID] = []
    for name in names or []:
        agent_id = resolve_router_choice(name, roster)
        if agent_id is not None and agent_id not in chosen:
            chosen.append(agent_id)
    return chosen


def router_prompt(
    message: InboundMessage,
    roster: list[RosterAgent],
    recent: list[str] | None = None,
) -> str:
    """The whole prompt. Short on purpose -- see the module docstring.

    `recent` is the last few lines of the conversation, oldest first. Without
    it every message is judged cold: "yes please" or "and the other one?" is
    unroutable on its own and obvious in context.
    """
    names = ", ".join(agent.name for agent in roster)
    context = (
        "Recent messages, oldest first:\n" + "\n".join(recent) + "\n\n"
        if recent
        else ""
    )
    return (
        "You route messages in a conversation that has both people and agents "
        "in it. Name every agent that should answer, most relevant first, or "
        "none of them.\n\n"
        f"Agents present: {names}\n\n"
        f"{context}"
        f"Message to route: {message.text}\n\n"
        "Choose an agent when the message is addressed to one of them, when it "
        "is addressed to the room as a whole, or when it asks something an "
        "agent here would answer. Name more than one only when the message "
        "genuinely asks several of them for something.\n\n"
        "Choose nobody when the message is one person talking to another -- "
        "arranging something between themselves, replying to each other, or "
        "chatting about anything the agents are not part of."
    )
