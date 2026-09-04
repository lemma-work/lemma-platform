"""What sits where in the prompt, and why the order is load-bearing.

Both providers cache a prefix. An OpenAI-compatible one caches on the literal
bytes, so everything after the first changed byte is re-read; Anthropic marks
explicit breakpoints over `[tools, system, messages]`. Either way the rule is
the same: the things that change often go last, behind the things that do not.

The prompt used to put the conversation's task list a third of the way in, so
every `write_todos` — the most frequent write there is — re-read everything
after it, which was most of the prompt.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.prompts import build_agent_instructions
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.capabilities.prompt_caching import (
    _ANTHROPIC_CACHE_TTL,
    PromptCachingCapability,
)

pytestmark = pytest.mark.unit


def _conversation_with_todos() -> Conversation:
    return Conversation(
        pod_id=uuid4(),
        user_id=uuid4(),
        metadata={"todos": [{"content": "Render the video", "done": False}]},
    )


def _agent(conversation: Conversation) -> Agent:
    return Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="butler",
        instruction="Answer briefly.",
        toolsets=["TODO"],
    )


class TestTheTaskListComesLast:
    def _instructions(self) -> str:
        conversation = _conversation_with_todos()
        return build_agent_instructions(
            agent=_agent(conversation),
            conversation=conversation,
            ctx=SimpleNamespace(),
        )

    def test_the_task_list_is_rendered_at_all(self) -> None:
        assert "Render the video" in self._instructions()

    def test_nothing_stable_sits_behind_it(self) -> None:
        """Anything after the rendered list is re-read on every `write_todos`.

        Matched on the rendered text, not the `# Task list` heading: the todo
        guidance fragment opens with the same heading, and it is static.
        """
        text = self._instructions()
        start = text.index("This conversation already has a task list")

        assert "# Agent Instructions" not in text[start:]
        # And it is genuinely the final section, not merely ahead of one other
        # marker. The previous version asserted on `# Runtime Context`, which
        # appears nowhere in this prompt, so it could never have failed.
        sections = text.split("\n\n---\n\n")
        assert "This conversation already has a task list" in sections[-1]

    def test_the_agent_instruction_still_arrives(self) -> None:
        assert "Answer briefly." in self._instructions()

    def test_a_conversation_with_no_plan_adds_nothing(self) -> None:
        conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

        text = build_agent_instructions(
            agent=_agent(conversation),
            conversation=conversation,
            ctx=SimpleNamespace(),
        )

        assert "This conversation already has a task list" not in text


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

    def test_an_openai_compatible_run_gets_affinity_keys_instead(self) -> None:
        """Those providers cache on the literal prefix; what they need is sticky
        routing so the turn lands on the replica holding it."""
        settings = self._settings(RuntimeProfileProtocol.OPENAI_COMPATIBLE)

        assert "openai_user" in settings
        assert "anthropic_cache_instructions" not in settings
