"""Canonical AGENTS.md locations for an agent's four memory scopes, and the
one predicate that says whether memory means anything to a given agent.

Every agent gets the same four fixed locations, computed the same way for the
pod-default assistant ("Lem", as users see it) as for any named agent: Lem's
synthetic ``Agent`` entity already has ``name == "pod_default"``
(``DEFAULT_POD_AGENT_NAME`` in ``app.core.authorization.delegation``), so no
special-casing is needed here — it slugifies like any other name.

A single source of truth matters because two different callers need to agree
on these paths exactly: whatever reads AGENTS.md content into the prompt, and
whatever tells the agent where its own writes should land. If those drifted
apart, an agent's notes would silently stop showing up in its own briefing.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from app.core.helpers.slug import slugify
from app.modules.agent.domain.entities import Agent
from app.modules.agent.domain.run_origin import RunOriginName
from app.modules.agent.domain.value_objects import AgentToolset

_POD_INDEX = "/memory/AGENTS.md"
_PERSONAL_INDEX = "/me/AGENTS.md"

# An agent name that slugifies to nothing (empty or all-symbol) still needs a
# stable, non-colliding folder rather than landing at the bare `.../agents/`.
_FALLBACK_SLUG = "agent"


@dataclass(frozen=True, slots=True)
class AgentMemoryPaths:
    """One agent's four canonical memory-index paths, plus its folder slug."""

    slug: str
    pod_agent_folder: str
    personal_agent_folder: str
    pod_index: str
    pod_agent_index: str
    personal_index: str
    personal_agent_index: str


def agent_memory_paths(agent: Agent) -> AgentMemoryPaths:
    return agent_memory_paths_for_name(agent.name)


def agent_memory_paths_for_name(name: str | None) -> AgentMemoryPaths:
    """The same four paths, from a name alone.

    The tools know their agent by name off the run context, not as an ``Agent``
    entity, and they must land on exactly the paths the brief reads back --
    that is the whole reason this module exists. Taking a name lets them share
    the rule instead of re-deriving a slug that could drift from it.
    """
    slug = slugify(name or "") or _FALLBACK_SLUG
    pod_agent_folder = f"/memory/agents/{slug}"
    personal_agent_folder = f"/me/agents/{slug}"
    return AgentMemoryPaths(
        slug=slug,
        pod_agent_folder=pod_agent_folder,
        personal_agent_folder=personal_agent_folder,
        pod_index=_POD_INDEX,
        pod_agent_index=f"{pod_agent_folder}/AGENTS.md",
        personal_index=_PERSONAL_INDEX,
        personal_agent_index=f"{personal_agent_folder}/AGENTS.md",
    )


# The toolsets that can actually reach a pod file. WORKSPACE_CLI writes through
# the shell (`lemma files write`), POD through `pod_write_file`.
_FILE_TOOLSETS = frozenset({AgentToolset.WORKSPACE_CLI, AgentToolset.POD})


def memory_is_active(toolsets: Collection[AgentToolset]) -> bool:
    """Whether this run should be taught, and shown, its memory.

    MEMORY carries no tools, so on its own it is a promise an agent cannot keep:
    told to write durable facts to `/memory`, given nothing to write with. Both
    the prompt fragment and the brief's ``## Your Memory`` section gate on this,
    so an agent that cannot act on memory is never told about it.

    Deliberately a predicate rather than an implication in
    ``resolve_toolset_names``: the only toolset that could be auto-added is POD,
    which also carries `pod_query` and `pod_write_record`. Granting table writes
    to obtain file reads is the wrong trade; the agent editor refuses the
    combination instead.
    """
    if AgentToolset.MEMORY not in toolsets:
        return False
    return any(toolset in _FILE_TOOLSETS for toolset in toolsets)


#: Origins whose turns a person is waiting on: typed in Lemma, or arrived on a
#: surface. A schedule, workflow, trigger or sub-agent turn has nobody waiting.
_ATTENDED_ORIGINS: frozenset[RunOriginName] = frozenset({"chat", "surface"})


def memory_kept_after_turn(
    *,
    memory_enabled: bool,
    in_process: bool,
    answers_outsiders: bool,
    origin: RunOriginName,
) -> bool:
    """Whether a run leaves its memory to a pass after it ends.

    Saving in the turn means reading and writing memory files before the
    answer, two or more model calls a person sits through for a fact they only
    mentioned. Such a run is told to write memory only when asked, and the
    pass (``events/memory_pass_work``) keeps the rest once the answer is out.

    Only where somebody is waiting: an unattended run loses nothing by saving
    as it goes, and knows its own work better than a pass reading it back. Only
    in process: the pass runs on the run's own model, which an Agent Host
    coding agent does not give Lemma. Never for outsiders, whose words are not
    written with a member's authority by anyone.
    """
    return (
        memory_enabled
        and in_process
        and not answers_outsiders
        and origin in _ATTENDED_ORIGINS
    )
