"""A run that can delegate is told how, on both harnesses.

A coding agent on Agent Host was offered `lemma_spawn_subagent` and never used
it: its own system prompt says to delegate with its built-in task tool, and
nothing in Lemma's said otherwise.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.modules.agent.capabilities.assembler import _instructions_for
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.prompt.templates import (
    build_agent_instructions,
    load_agent_host_runtime_prompt,
    load_subagents_prompt,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.subagents.pydantic_adapter import subagents_toolset

_FRAGMENT_HEADING = "# Sub-agents"


def _prompt(
    toolsets: list[AgentToolset], *, include_toolset_prompts: bool = True
) -> str:
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="lead",
        instruction="",
    )
    return build_agent_instructions(
        agent=agent,
        conversation=conversation,
        ctx=SimpleNamespace(run_toolsets=toolsets),
        include_toolset_prompts=include_toolset_prompts,
    )


def test_a_remote_run_that_can_delegate_is_told_to_delegate_through_lemma():
    prompt = _prompt([AgentToolset.SUBAGENTS, AgentToolset.WAIT])

    assert _FRAGMENT_HEADING in prompt
    flat = " ".join(prompt.split())
    assert "never through a task or sub-agent tool of your own runtime" in flat
    assert "`wait_for(subagent_run_id=...)`" in flat


def test_a_run_that_cannot_delegate_is_not_told_about_it():
    """A sub-agent has no SUBAGENTS toolset, and must not be sent to spawn."""
    assert _FRAGMENT_HEADING not in _prompt([AgentToolset.WAIT])


def test_the_in_process_run_gets_the_same_text_once():
    """In process the fragment rides on the toolset's capability, deferred or
    not, so the system prompt itself must not carry a second copy."""
    guidance = _instructions_for(subagents_toolset)

    assert guidance is not None, "the in-process path lost the fragment"
    assert guidance[1]() == load_subagents_prompt()
    assert _FRAGMENT_HEADING not in _prompt(
        [AgentToolset.SUBAGENTS], include_toolset_prompts=False
    )


def test_an_agent_host_run_is_told_lemma_tools_carry_a_prefix():
    """The fragments name tools as the in-process run sees them; the MCP
    listing prefixes them. The runtime prompt says so once, for every one, with
    or without host execution."""
    for host_execution in (False, True):
        flat = " ".join(
            load_agent_host_runtime_prompt(host_execution=host_execution).split()
        )
        assert "`ask_user` is `lemma_ask_user`" in flat
