"""Which toolsets a run is entitled to, before any of them are built.

Two rules decide this and both are easy to lose in the middle of assembly, so
they live here where they can be read and tested without a database:

* **`USER_INTERACTION` implies `SKILLS`.** `display_resource` can only author
  WIDGET content after reading the built-in `lemma-widget` skill, so an agent
  granted one without the other would be given a tool it cannot use correctly.
  This grants skill *reading* only -- no pod, shell, network or resource access
  rides along with it.
* **Depth is one.** A run that is itself a spawned sub-agent gets neither the
  sub-agent controls nor the `agent_<name>` spawn tools, so a sub-agent cannot
  spawn its own.

The depth rule reads `is_sub_agent`, stamped by `SubAgentService.spawn`, and
deliberately not `parent_id`: a conversation can have a parent -- pinned under a
project, say -- without being a sub-agent, and those keep their spawning
ability.
"""

from __future__ import annotations

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

# Withheld from a sub-agent, each for its own reason:
#
# SUBAGENTS   -- the depth rule itself.
# SNOOZE      -- a sleeping child blocks its parent's tool call while the parent
#                is still mid-run and subject to its own limits.
# MESSAGING   -- a sub-agent is an implementation detail of its parent's turn,
#                and a colleague receiving a message from one has no way to
#                place it. Whatever needs saying, the parent should say.
_SUB_AGENT_WITHHELD = frozenset(
    {AgentToolset.SUBAGENTS, AgentToolset.SNOOZE, AgentToolset.MESSAGING}
)


def is_sub_agent_run(conversation: Conversation | None) -> bool:
    """Whether this conversation was spawned as somebody else's sub-agent."""
    if conversation is None:
        return False
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    return bool(metadata.get("is_sub_agent"))


def resolve_toolset_names(
    agent: Agent | None, conversation: Conversation | None
) -> tuple[list[AgentToolset], bool]:
    """The toolsets this run may have, and whether it may spawn sub-agents.

    The pod default assistant -- a run with no specific agent -- gets the fixed
    default set. A user-created agent gets what it was configured with, plus the
    narrow runtime dependencies needed to use those correctly.
    """
    names = list(agent.toolsets if agent is not None else POD_DEFAULT_AGENT_TOOLSETS)
    if AgentToolset.USER_INTERACTION in names and AgentToolset.SKILLS not in names:
        names.append(AgentToolset.SKILLS)
    allow_subagents = not is_sub_agent_run(conversation)
    if not allow_subagents:
        names = [name for name in names if name not in _SUB_AGENT_WITHHELD]
    return names, allow_subagents
