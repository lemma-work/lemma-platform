"""Toolset resolver for Agent harnesses."""

from __future__ import annotations

from collections.abc import Iterable
from pydantic_ai.toolsets import AbstractToolset
from app.modules.agent.tools.context import ConversationContext

from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.browser.pydantic_adapter import browser_toolset
from app.modules.agent.tools.connectors.pydantic_adapter import connectors_toolset
from app.modules.agent.tools.messaging.pydantic_adapter import messaging_toolset
from app.modules.agent.tools.speech.pydantic_adapter import speech_toolset
from app.modules.agent.tools.pod.pydantic_adapter import pod_toolset
from app.modules.agent.tools.skills.pydantic_adapter import skills_toolset
from app.modules.agent.tools.waiting.pydantic_adapter import waiting_toolset
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
    AgentToolset.BROWSER,
    AgentToolset.POD,
    AgentToolset.USER_INTERACTION,
    AgentToolset.SKILLS,
    AgentToolset.WEB_SEARCH,
    AgentToolset.SUBAGENTS,
    AgentToolset.SPEECH,
    AgentToolset.TODO,
    # Reaching a colleague, and being able to wait for their answer. These two
    # are one capability: `message_user` does not block, so without `wait_for` the
    # agent is told to send and then has no way to be around when the reply
    # lands. Both are deferred (see EXTRA_TOOLSETS) so neither shows up in the
    # prompt prefix of an ordinary chat.
    AgentToolset.MESSAGING,
    AgentToolset.WAIT,
    # Memory contributes no tools -- see `_CAPABILITY_ONLY_TOOLSETS`. It is in
    # this list so Lem is taught the memory contract and gets its AGENTS.md
    # scopes loaded into every brief; the reading and writing happen through
    # WORKSPACE_CLI and POD, which are already here.
    AgentToolset.MEMORY,
)

_TOOLSET_BY_NAME: dict[AgentToolset, AbstractToolset[ConversationContext]] = {
    AgentToolset.WORKSPACE_CLI: workspace_cli_toolset,
    AgentToolset.BROWSER: browser_toolset,
    AgentToolset.SKILLS: skills_toolset,
    AgentToolset.WEB_SEARCH: web_search_toolset,
    AgentToolset.USER_INTERACTION: user_interaction_toolset,
    AgentToolset.SPEECH: speech_toolset,
    AgentToolset.POD: pod_toolset,
    AgentToolset.SUBAGENTS: subagents_toolset,
    AgentToolset.VIEW_IMAGE: view_image_toolset,
    AgentToolset.CONNECTORS: connectors_toolset,
    AgentToolset.WAIT: waiting_toolset,
    AgentToolset.MESSAGING: messaging_toolset,
}

# Toolsets with no entry in ``_TOOLSET_BY_NAME``, for either of two reasons:
# they are not static singletons and must be realized per-conversation (TODO
# needs conversation-scoped storage), or they carry no tools at all and exist
# only to contribute prompt guidance (MEMORY). Both are skipped by
# ``resolve_agent_toolsets`` — which indexes ``_TOOLSET_BY_NAME`` unguarded, so
# a name missing from BOTH is a KeyError — and handled by the capability
# assembler instead.
_CAPABILITY_ONLY_TOOLSETS: frozenset[AgentToolset] = frozenset(
    {AgentToolset.TODO, AgentToolset.MEMORY}
)

# "Extra" toolsets are heavy/optional surfaces the in-process LEMMA harness loads
# lazily (deferred) over the conversation MCP server instead of in the prompt
# prefix. The singleton object identities let the capability assembler split the
# assembled toolset list into visible-core vs deferred-extra.
EXTRA_TOOLSETS: tuple[AgentToolset, ...] = (
    # POD is deliberately NOT here, and it is the one entry whose absence needs
    # a reason. It was deferred like the rest, and the pod tools then went
    # almost unused — most conversations that touched pod files did it through
    # the `lemma` CLI in a shell instead, and paid for it in CLI usage errors.
    #
    # Deferral was not the whole cause: the workspace prompt taught the CLI
    # equivalent of most of those tools in the *visible* prefix, so the bypass
    # was cheaper than the search. Both halves changed together. Visible POD
    # costs real prefix budget in every pod-default prompt, which is the trade
    # being made on purpose: the tools that touch the pod's own data are the
    # ones that must not need finding first.
    #
    # An org with a couple of MCP servers installed can expose thousands of
    # operations. Deferred so the model finds them via search_tools rather than
    # carrying the surface in every prompt prefix.
    AgentToolset.CONNECTORS,
    # Subagent delegation is deferred too: top-level agents discover spawn/interact/
    # query via search_tools rather than carrying them in every prompt prefix.
    # (RunToolAssembler still drops SUBAGENTS entirely for sub-agent conversations
    # before the capability assembler runs, so sub-agents never get them.)
    AgentToolset.SUBAGENTS,
    # Messaging and waiting are deferred for a UX reason rather than a size one:
    # the pod assistant is the interactive chat, and an assistant carrying
    # "message a colleague" and "go to sleep" in its visible prefix reaches for
    # them. Behind ToolSearch it has to go looking first — the same bar as
    # spawning a sub-agent. Their *instructions* still ride in the prefix; see
    # `_deferred_capability`, because hiding the contract while advertising the
    # tool is the worst of both.
    AgentToolset.MESSAGING,
    AgentToolset.WAIT,
    # Asking a person to sign in is a deliberate step past `web_fetch`, which
    # already covers ordinary research in the visible prefix. This used to be
    # five schemas for driving a page as well, which is what
    # `test_pod_default_visible_toolset_is_slim` exists to refuse; the driving
    # is now `agent-browser` through `exec_command`, so what is left behind
    # ToolSearch is the one tool that pauses the run.
    AgentToolset.BROWSER,
    # Speech is a minority of turns and now three tools rather than two, so it
    # is behind the search like the browser. It went the other way to POD for
    # the opposite reason: nobody reaches for `say` by accident, and a prompt
    # that mentions voice is a prompt where finding it costs one call.
    AgentToolset.SPEECH,
)
EXTRA_TOOLSET_OBJECTS: tuple[AbstractToolset[ConversationContext], ...] = tuple(
    _TOOLSET_BY_NAME[name] for name in EXTRA_TOOLSETS
)


def resolve_agent_toolsets(
    selected_toolsets: Iterable[AgentToolset],
) -> list[AbstractToolset[ConversationContext]]:
    """Resolve the given toolset enums to Pydantic AI toolset instances.

    Resolves exactly what is passed (deduplicated, order-preserving). Callers
    decide the set — there are no implicit defaults. Capability-only toolsets
    (e.g. TODO) are skipped here and assembled separately as capabilities.
    """
    resolved: list[AbstractToolset[ConversationContext]] = []
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
