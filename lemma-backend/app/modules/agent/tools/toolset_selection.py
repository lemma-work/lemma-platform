"""Which toolsets a run is entitled to, before any of them are built.

An agent's toolsets come from three places, and only one of them is a person's
decision:

* **Declared** — what the author chose, persisted on ``Agent.toolsets`` and
  shown in the agent editor. Reserved for abilities where the answer is a
  judgement rather than a fact: a sandbox shell, the open web, delegating to
  other agents, voice, memory.
* **Always on** — abilities every agent should simply have. Asking a person a
  question and requesting approval are the two that matter most: withholding
  ``request_approval`` does not make an agent safer, it removes the seam where a
  human gets to say no.
* **Derived** — implied by a grant the author already made. Granting a folder
  and then separately ticking "pod data" asks the same question twice, and the
  failure mode is silent: the agent cannot see the folder you just gave it.

The rules below all live here, rather than in the assembler, because three
callers must agree on the answer exactly — the runner, the conversation/pod MCP
server, and the approval executor. A tool that one of them believes in and
another does not is a tool that lists and then fails to dispatch.

Derivation reads grants, but this module stays synchronous and pure: the caller
loads an ``AgentGrantSummary`` (one query, shared with the callable-tool factory
that was already making it) and passes it in. That keeps every rule here
testable without a database.

**Depth is one.** A run that is itself a spawned sub-agent gets neither the
sub-agent controls nor the ``agent_<name>`` spawn tools, so a sub-agent cannot
spawn its own. That subtraction happens last, after the always-on set, or
sub-agents would be handed back the very toolsets they are withheld from.

The depth rule reads ``is_sub_agent``, stamped by ``SubAgentService.spawn``, and
deliberately not ``parent_id``: a conversation can have a parent -- pinned under
a project, say -- without being a sub-agent, and those keep their spawning
ability.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.authorization.context import ResourceType
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

# What the agent editor offers and ``Agent.toolsets`` is expected to hold. Each
# one is a real decision with a cost or a blast radius behind it; everything
# else an agent can do is either universal or already answered by a grant.
DECLARABLE_TOOLSETS: tuple[AgentToolset, ...] = (
    AgentToolset.WORKSPACE_CLI,
    AgentToolset.WEB_SEARCH,
    AgentToolset.BROWSER,
    AgentToolset.SUBAGENTS,
    AgentToolset.SPEECH,
    AgentToolset.MEMORY,
)

# What a new agent starts with when its creator did not say. Both are cheap and
# both are things people turn on immediately anyway: an agent that cannot look
# anything up answers from a two-year-old training set, and one that cannot
# remember re-learns the same fact every conversation. Turning either off is one
# click in the agent's Tools; discovering months later that it was never on is
# not.
#
# WORKSPACE_CLI, SUBAGENTS and SPEECH stay off: a sandbox, fan-out and a voice
# bill are decisions, not conveniences.
#
# Applies only where the field is *absent* -- an explicit ``[]`` from a bundle
# import or the agent editor still means exactly none.
NEW_AGENT_DEFAULT_TOOLSETS: tuple[AgentToolset, ...] = (
    AgentToolset.WEB_SEARCH,
    AgentToolset.MEMORY,
)

# Given to every agent. None of these reaches pod data, the internet, or a
# sandbox; each is either a way to involve a person or conversation-scoped
# scratch.
#
# SKILLS was already implied by USER_INTERACTION (display_resource cannot author
# WIDGET content without reading the built-in lemma-widget skill), and with
# USER_INTERACTION universal the implication was universal too -- so it is
# stated here rather than derived from a toolset that is now always present.
#
# MESSAGING and SNOOZE are one capability in practice: `message_user` does not
# block, so an agent given the first without the second can send and then has no
# way to be around when the reply lands. Messaging is fenced to pod members by
# `resolve_pod_recipient`, which joins through PodMember and returns None for
# anyone outside the pod -- so "reach a colleague" cannot become "reach a
# stranger", and the capability is the pod's own membership list.
ALWAYS_ON_TOOLSETS: tuple[AgentToolset, ...] = (
    AgentToolset.USER_INTERACTION,
    AgentToolset.SKILLS,
    AgentToolset.SNOOZE,
    AgentToolset.MESSAGING,
    AgentToolset.TODO,
)

# Resource types whose grant implies the agent should be able to reach pod data
# at all. Files and tables are the same toolset (`POD`), and the grant is what
# actually scopes it -- the toolset only decides whether the tools are offered.
_POD_DATA_RESOURCES = frozenset(
    {
        ResourceType.FOLDER,
        ResourceType.DOCUMENT,
        ResourceType.DATASTORE_TABLE,
        ResourceType.DATASTORE_RECORD,
    }
)

# Likewise for connected apps. The connector toolset never granted anything on
# its own -- reaching an app has always needed a per-app grant as well -- so
# requiring both was asking for the same permission twice.
_CONNECTOR_RESOURCES = frozenset(
    {
        ResourceType.CONNECTOR,
        ResourceType.CONNECTOR_ACCOUNT,
        ResourceType.CONNECTOR_AUTH_CONFIG,
    }
)

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


@dataclass(frozen=True, slots=True)
class AgentGrantSummary:
    """What one agent has been granted, in the shape toolset selection needs.

    Loaded once per run from a single query and shared with
    ``AgentCallableToolFactory``, which was already reading the same table for
    its ``function_<name>`` and ``agent_<name>`` tools.

    An empty summary is the honest default for a caller that has no agent to
    look up (the pod default assistant has no grants of its own -- it runs with
    the user's permissions and gets its toolsets from the fixed default set).
    """

    function_ids: tuple[str, ...] = ()
    agent_ids: tuple[str, ...] = ()
    has_pod_data: bool = False
    has_connectors: bool = False

    @classmethod
    def from_grants(
        cls,
        rows: list[tuple[str, object]],
        *,
        function_ids: tuple[str, ...] = (),
        agent_ids: tuple[str, ...] = (),
    ) -> "AgentGrantSummary":
        """Build from ``(resource_type, resource_id)`` rows of one agent's grants."""
        resource_types = {str(resource_type) for resource_type, _ in rows}
        return cls(
            function_ids=function_ids,
            agent_ids=agent_ids,
            has_pod_data=bool(
                resource_types & {member.value for member in _POD_DATA_RESOURCES}
            ),
            has_connectors=bool(
                resource_types & {member.value for member in _CONNECTOR_RESOURCES}
            ),
        )


@dataclass(frozen=True, slots=True)
class ResolvedToolsets:
    """The effective toolsets for one run, and why each one is there.

    The breakdown is not decoration: the agent editor shows a person only what
    they chose, while support questions ("why can this agent read that folder?")
    are answered by the derived half.
    """

    names: list[AgentToolset] = field(default_factory=list)
    allow_subagents: bool = True
    derived: frozenset[AgentToolset] = frozenset()


def is_sub_agent_run(conversation: Conversation | None) -> bool:
    """Whether this conversation was spawned as somebody else's sub-agent."""
    if conversation is None:
        return False
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    return bool(metadata.get("is_sub_agent"))


def derived_toolsets(grants: AgentGrantSummary | None) -> frozenset[AgentToolset]:
    """Toolsets a grant already implies, so nobody has to tick them twice."""
    if grants is None:
        return frozenset()
    derived: set[AgentToolset] = set()
    if grants.has_pod_data:
        derived.add(AgentToolset.POD)
    if grants.has_connectors:
        derived.add(AgentToolset.CONNECTORS)
    return frozenset(derived)


def resolve_toolsets(
    agent: Agent | None,
    conversation: Conversation | None,
    *,
    grants: AgentGrantSummary | None = None,
) -> ResolvedToolsets:
    """The toolsets this run may have, and whether it may spawn sub-agents.

    The pod default assistant gets the fixed default set. A user-created agent
    gets what it was configured with, plus everything universal, plus whatever
    its grants imply.

    Keyed on the agent's *kind*, not on whether an agent was passed. It used to
    be the latter, back when the assistant was the run with no agent at all;
    now that it has a row, `agent is not None` is true for it too, and the row
    stores `toolsets = []` on purpose -- so reading the column would hand the
    assistant an empty toolset and leave it with nothing but the always-on set.
    `resolve_agent` substitutes the same constant, and this stays correct for
    callers that did not come through it.

    Order matters at the end: the sub-agent subtraction runs last, so a child
    run does not receive MESSAGING and SNOOZE back through the always-on set.
    """
    is_pod_default = agent is None or agent.kind is AgentKind.POD_DEFAULT
    declared = list(POD_DEFAULT_AGENT_TOOLSETS if is_pod_default else agent.toolsets)
    implied = derived_toolsets(grants)

    names: list[AgentToolset] = []
    seen: set[AgentToolset] = set()
    for name in (*declared, *ALWAYS_ON_TOOLSETS, *sorted(implied, key=str)):
        try:
            toolset = AgentToolset(name)
        except ValueError:  # pragma: no cover - a stale name on an old row
            continue
        if toolset in seen:
            continue
        seen.add(toolset)
        names.append(toolset)

    allow_subagents = not is_sub_agent_run(conversation)
    if not allow_subagents:
        names = [name for name in names if name not in _SUB_AGENT_WITHHELD]
    return ResolvedToolsets(
        names=names, allow_subagents=allow_subagents, derived=implied
    )


def resolve_toolset_names(
    agent: Agent | None,
    conversation: Conversation | None,
    *,
    grants: AgentGrantSummary | None = None,
) -> tuple[list[AgentToolset], bool]:
    """``(names, allow_subagents)`` -- the tuple most callers actually want."""
    resolved = resolve_toolsets(agent, conversation, grants=grants)
    return resolved.names, resolved.allow_subagents
