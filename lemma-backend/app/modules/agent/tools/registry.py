"""Toolset resolver for Agent harnesses."""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.speech.pydantic_adapter import speech_toolset
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset
from app.modules.agent.tools.skills.pydantic_adapter import skills_toolset
from app.modules.agent.tools.subagents.pydantic_adapter import subagents_toolset
from app.modules.agent.tools.user_interaction.pydantic_adapter import (
    user_interaction_toolset,
)
from app.modules.agent.tools.web.pydantic_adapter import web_search_toolset
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    view_image_toolset,
    workspace_cli_toolset,
)

# The pod default assistant runs with the user's own permissions and gets a
# fixed, batteries-included toolset. User-created agents get EXACTLY the toolsets
# they were created with — no implicit defaults are added.
POD_DEFAULT_AGENT_TOOLSETS = (
    AgentToolset.WORKSPACE_CLI,
    AgentToolset.POD,
    AgentToolset.USER_INTERACTION,
    AgentToolset.SKILLS,
    AgentToolset.WEB_SEARCH,
    AgentToolset.SUBAGENTS,
    AgentToolset.SPEECH,
    AgentToolset.TODO,
)

_TOOLSET_BY_NAME: dict[AgentToolset, object] = {
    AgentToolset.WORKSPACE_CLI: workspace_cli_toolset,
    AgentToolset.SKILLS: skills_toolset,
    AgentToolset.WEB_SEARCH: web_search_toolset,
    AgentToolset.USER_INTERACTION: user_interaction_toolset,
    AgentToolset.SPEECH: speech_toolset,
    AgentToolset.POD: pod_toolset,
    AgentToolset.SUBAGENTS: subagents_toolset,
    AgentToolset.VIEW_IMAGE: view_image_toolset,
}

# Toolsets that are NOT static singletons — they are realized per-conversation as
# pydantic-ai capabilities (e.g. TODO needs conversation-scoped storage). They are
# skipped by ``resolve_agent_toolsets`` and handled by the capability assembler.
_CAPABILITY_ONLY_TOOLSETS: frozenset[AgentToolset] = frozenset({AgentToolset.TODO})

# "Extra" toolsets are heavy/optional surfaces the in-process LEMMA harness loads
# lazily (deferred) over the conversation MCP server instead of in the prompt
# prefix. The singleton object identities let the capability assembler split the
# assembled toolset list into visible-core vs deferred-extra.
EXTRA_TOOLSETS: tuple[AgentToolset, ...] = (
    AgentToolset.POD,
    # Subagent delegation is deferred too: top-level agents discover spawn/interact/
    # query via search_tools rather than carrying them in every prompt prefix.
    # (RunToolAssembler still drops SUBAGENTS entirely for sub-agent conversations
    # before the capability assembler runs, so sub-agents never get them.)
    AgentToolset.SUBAGENTS,
)
EXTRA_TOOLSET_OBJECTS: tuple[object, ...] = tuple(
    _TOOLSET_BY_NAME[name] for name in EXTRA_TOOLSETS
)


def resolve_agent_toolsets(
    selected_toolsets: Iterable[AgentToolset],
) -> list[object]:
    """Resolve the given toolset enums to Pydantic AI toolset instances.

    Resolves exactly what is passed (deduplicated, order-preserving). Callers
    decide the set — there are no implicit defaults. Capability-only toolsets
    (e.g. TODO) are skipped here and assembled separately as capabilities.
    """
    resolved: list[object] = []
    seen: set[AgentToolset] = set()
    for toolset_name in selected_toolsets:
        if toolset_name in seen or toolset_name in _CAPABILITY_ONLY_TOOLSETS:
            continue
        seen.add(toolset_name)
        resolved.append(_TOOLSET_BY_NAME[toolset_name])
    return resolved


__all__ = [
    "POD_DEFAULT_AGENT_TOOLSETS",
    "EXTRA_TOOLSETS",
    "EXTRA_TOOLSET_OBJECTS",
    "resolve_agent_toolsets",
]
