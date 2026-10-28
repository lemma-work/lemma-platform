"""What sits where in the prompt, and why the order is load-bearing.

Both providers cache a prefix. An OpenAI-compatible one caches on the literal
bytes, so everything after the first changed byte is re-read; Anthropic marks
explicit breakpoints over `[tools, system, messages]`. Either way the system
prompt sits in front of the whole replayed history, so a byte of it that changes
between two runs of one conversation re-reads every message behind it.

So nothing that changes run to run is in it. The task list -- rewritten by
every `write_todos`, the most frequent write there is -- the attached doc,
memory and open requests ride in the run's context part instead
(`domain/run_context_prompt`).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.prompt.templates import build_agent_instructions
from app.modules.agent.domain.prompt.run_context import task_list_section
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    _ANTHROPIC_CACHE_TTL,
    PromptCaching as PromptCachingCapability,
)
from app.modules.agent.domain.toolsets import (
    resolve_toolset_names,
)
from app.modules.agent.domain.todos import Todo

pytestmark = pytest.mark.unit


def _conversation(todo: str | None = "Render the video") -> Conversation:
    return Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        agent_id=uuid4(),
        todos=[Todo(content=todo)] if todo else [],
    )


def _agent(conversation: Conversation) -> Agent:
    # A configuration an agent can actually be saved with: TODO is always on and
    # never declared, which an earlier version of this test did.
    return Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="butler",
        instruction="Answer briefly.",
        toolsets=["WEB_SEARCH"],
    )


class TestNothingThatChangesBetweenRunsIsInTheSystemPrompt:
    def _instructions(self, conversation: Conversation, **ctx: object) -> str:
        return build_agent_instructions(
            agent=_agent(conversation),
            conversation=conversation,
            ctx=SimpleNamespace(**ctx),
        )

    def test_the_task_list_is_rendered_for_the_run(self) -> None:
        conversation = _conversation()
        run_toolsets, _ = resolve_toolset_names(_agent(conversation), conversation)

        assert "Render the video" in task_list_section(
            conversation, toolsets=run_toolsets
        )
        assert "Render the video" not in self._instructions(conversation)

    def test_a_checklist_update_leaves_the_system_prompt_byte_identical(self) -> None:
        before = _conversation("Render the video")
        after = before.model_copy(update={"todos": [Todo(content="Render", done=True)]})

        assert self._instructions(before) == self._instructions(after)

    def test_a_doc_edit_leaves_the_system_prompt_byte_identical(self) -> None:
        conversation = _conversation()

        assert self._instructions(
            conversation, attached_document="# The open doc\nv1"
        ) == self._instructions(conversation, attached_document="# The open doc\nv2")

    def test_the_agent_instruction_still_arrives(self) -> None:
        assert "Answer briefly." in self._instructions(_conversation())


class TestAnthropicCachesToolDefinitionsToo:
    def _settings(self, protocol: RuntimeProfileProtocol) -> dict:
        return PromptCachingCapability(
            conversation_id=uuid4(), protocol=protocol
        ).get_model_settings()

    def test_the_tool_array_gets_its_own_breakpoint(self) -> None:
        """The cache is a prefix over [tools, system, messages], and the tool
        array changes mid-run: `search_tools` reveals deferred tools on demand,
        which is the entire point of deferring them."""
        settings = self._settings(RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)

        # The value, not just the key: `False` or `None` would pass a presence
        # check and disable the very thing being asserted.
        assert settings["anthropic_cache_tool_definitions"] == _ANTHROPIC_CACHE_TTL

    def test_the_instruction_breakpoint_is_still_there(self) -> None:
        settings = self._settings(RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)

        assert settings["anthropic_cache_instructions"] == _ANTHROPIC_CACHE_TTL

    def test_the_message_history_gets_a_breakpoint(self) -> None:
        """Without one, every step of a run re-read every message before it."""
        settings = self._settings(RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)

        assert settings["anthropic_cache_messages"] == _ANTHROPIC_CACHE_TTL

    def test_an_openai_compatible_run_gets_affinity_keys_instead(self) -> None:
        """Those providers cache on the literal prefix; what they need is sticky
        routing so the turn lands on the replica holding it."""
        settings = self._settings(RuntimeProfileProtocol.OPENAI_COMPATIBLE)

        assert "openai_user" in settings
        assert "anthropic_cache_instructions" not in settings


def test_no_heading_is_said_twice_across_the_prompt_and_the_run_context() -> None:
    """The todo guidance and the stored list both opened `# Task list`, so the
    model read one section title over two different things."""
    from app.modules.agent.domain.prompt.run_context import render_run_context

    conversation = _conversation()
    run_toolsets, _ = resolve_toolset_names(None, conversation)
    system_prompt = build_agent_instructions(
        agent=_agent(conversation),
        conversation=conversation,
        ctx=SimpleNamespace(run_toolsets=run_toolsets),
    )
    run_context = render_run_context(
        sections=[task_list_section(conversation, toolsets=run_toolsets)]
    )

    headings = [
        line
        for line in f"{system_prompt}\n{run_context}".splitlines()
        if line.startswith("# ")
    ]
    assert "# Your current task list" in headings
    assert len(headings) == len(set(headings)), headings
