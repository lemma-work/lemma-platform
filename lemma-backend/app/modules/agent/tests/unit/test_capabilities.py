"""Unit tests for the LEMMA-harness capability layer.

Covers: core/extra partitioning, current-time trailing injection, prompt-cache
session affinity, and that deferred extra tools stay out of the initial request
(discoverable via tool search) — all without a live stack.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.capabilities.assembler import (
    _agent_has_toolset,
    _partition_core_extra,
)
from types import SimpleNamespace

from app.modules.agent.capabilities.current_time import CurrentTimeCapability
from app.modules.agent.capabilities.prompt_caching import PromptCachingCapability
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.registry import (
    pod_toolset,
    web_search_toolset,
    workspace_cli_toolset,
)


def test_partition_core_extra_splits_pod_into_extra_for_pod_default():
    core, extra = _partition_core_extra(
        [workspace_cli_toolset, pod_toolset, web_search_toolset],
        is_pod_default=True,
    )
    assert pod_toolset in extra
    assert workspace_cli_toolset in core
    assert web_search_toolset in core
    assert pod_toolset not in core


def test_partition_core_extra_keeps_everything_core_for_user_created_agent():
    """User-created agents that configured POD/SUBAGENTS get them injected
    directly (no ToolSearch round-trip) — only the pod-default agent defers."""
    core, extra = _partition_core_extra(
        [workspace_cli_toolset, pod_toolset, web_search_toolset],
        is_pod_default=False,
    )
    assert extra == []
    assert pod_toolset in core
    assert workspace_cli_toolset in core
    assert web_search_toolset in core


def test_prompt_caching_keys_on_conversation_id():
    conversation_id = uuid4()
    settings = PromptCachingCapability(
        conversation_id=conversation_id
    ).get_model_settings()
    affinity = str(conversation_id)
    assert settings["openai_user"] == affinity
    assert settings["openai_prompt_cache_key"] == affinity
    # Provider-specific headers (e.g. x-session-affinity for Fireworks) are
    # added by subclasses registered via configure_caching_capability().
    assert "extra_headers" not in settings


def test_agent_has_toolset_detects_todo():
    assert _agent_has_toolset(
        SimpleNamespace(toolsets=[AgentToolset.TODO]), AgentToolset.TODO
    )
    assert not _agent_has_toolset(
        SimpleNamespace(toolsets=[AgentToolset.WEB_SEARCH]), AgentToolset.TODO
    )


def test_web_search_capability_bundles_tool_and_prompt():
    from app.modules.agent.capabilities.web_search import WebSearchCapability
    from app.modules.agent.tools.graceful_toolset import GracefulToolset

    cap = WebSearchCapability()
    # The toolset is graceful-wrapped so a web-search failure becomes a tool
    # response rather than aborting the run.
    toolset = cap.get_toolset()
    assert isinstance(toolset, GracefulToolset)
    assert toolset.wrapped is web_search_toolset
    assert "Web Search" in cap.get_instructions()


@pytest.mark.anyio
async def test_assembler_returns_capabilities_for_every_visible_toolset():
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling
    from app.modules.agent.capabilities.current_time import (
        CurrentTimeCapability as _CT,
    )
    from app.modules.agent.capabilities.prompt_caching import (
        PromptCachingCapability as _PC,
    )
    from app.modules.agent.capabilities.web_search import WebSearchCapability
    from app.modules.agent.capabilities.instructed_toolset import (
        InstructedToolsetCapability,
    )

    # No extra (pod/image/audio) toolsets → no MCP/token path, no network.
    capabilities = await build_lemma_harness_tooling(
        uow_factory=None,
        agent=SimpleNamespace(
            toolsets=[AgentToolset.WEB_SEARCH, AgentToolset.WORKSPACE_CLI]
        ),
        ctx=SimpleNamespace(conversation_id=uuid4(), is_pod_default_agent=True),
        full_toolsets=[web_search_toolset, workspace_cli_toolset],
        agent_run_id=uuid4(),
        model_name="m",
        enable_prompt_caching=True,
    )
    assert any(isinstance(c, WebSearchCapability) for c in capabilities)
    # The workspace CLI toolset is wrapped as an instructed capability that carries
    # its usage fragment.
    assert any(
        isinstance(c, InstructedToolsetCapability)
        and c.get_serialization_name() == "workspace_cli"
        for c in capabilities
    )
    assert any(isinstance(c, _CT) for c in capabilities)
    assert any(isinstance(c, _PC) for c in capabilities)
    # No extra tools → no ToolSearch capability.
    assert not any(type(c).__name__ == "ToolSearch" for c in capabilities)


class _FakeRepo:
    def __init__(self, store: dict) -> None:
        self._store = store

    async def get_conversation_metadata_key(self, _cid, key: str):
        return self._store.get(key)

    async def set_conversation_metadata_key(self, _cid, key: str, value) -> None:
        self._store[key] = value


class _FakeUoW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def commit(self) -> None:
        pass


@pytest.mark.anyio
async def test_write_todos_merges_lines_and_flips_status(monkeypatch):
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    from app.modules.agent.capabilities import todo_storage as storage_mod
    from app.modules.agent.capabilities.todo import build_todo_capability
    from app.modules.agent.tools.context import BaseAgentContext

    store: dict = {"is_sub_agent": True}  # a sibling metadata key
    monkeypatch.setattr(
        storage_mod, "ConversationRepository", lambda _uow: _FakeRepo(store)
    )

    capability = build_todo_capability(
        uow_factory=lambda: _FakeUoW(), conversation_id=uuid4()
    )
    toolset = capability.get_toolset()
    run_ctx = RunContext(
        deps=BaseAgentContext(
            user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    async def call(name: str, args: dict):
        prepared = await toolset.for_run(run_ctx)
        async with prepared:
            tools = await prepared.get_tools(run_ctx)
            tool = tools[name]
            validated = tool.args_validator.validate_python(
                args, context=run_ctx.validation_context
            )
            return await prepared.call_tool(name, validated, run_ctx, tool)

    # Exactly one tool now (write_todos), no separate status updater.
    prepared = await toolset.for_run(run_ctx)
    async with prepared:
        assert set((await prepared.get_tools(run_ctx)).keys()) == {"write_todos"}

    # Plain-text and checkbox lines both parse; all start not-done. The full list
    # is always returned as rendered checklist lines.
    result = await call("write_todos", {"todos": ["ship it", "- [ ] test it"]})
    assert result["success"] is True
    assert result["todos"] == ["- [ ] ship it", "- [ ] test it"]
    # Sibling metadata key is untouched (todos live under their own key).
    assert store["is_sub_agent"] is True
    assert "todos" in store

    # A SINGLE line flips just that task (matched by text); the rest is preserved.
    result = await call("write_todos", {"todos": ["- [x] ship it"]})
    assert result["success"] is True
    assert result["todos"] == ["- [x] ship it", "- [ ] test it"]

    # A new (unmatched) line is appended; a leading '* [*]' counts as done.
    result = await call("write_todos", {"todos": ["* [*] write docs"]})
    assert result["success"] is True
    assert result["todos"] == [
        "- [x] ship it",
        "- [ ] test it",
        "- [x] write docs",
    ]

    # Matching ignores case/whitespace, and stored text keeps its original casing.
    result = await call("write_todos", {"todos": ["- [x]   TEST IT  "]})
    assert result["todos"][1] == "- [x] test it"

    # A no-checkbox line for an existing task resets it to not-done (replace
    # semantics: the line's state is authoritative for the matched task).
    result = await call("write_todos", {"todos": ["ship it"]})
    assert result["todos"][0] == "- [ ] ship it"
    assert store["is_sub_agent"] is True


@pytest.mark.anyio
async def test_write_todos_guards_empty_and_blank_calls(monkeypatch):
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    from app.modules.agent.capabilities import todo_storage as storage_mod
    from app.modules.agent.capabilities.todo import build_todo_capability
    from app.modules.agent.tools.context import BaseAgentContext

    store: dict = {}
    monkeypatch.setattr(
        storage_mod, "ConversationRepository", lambda _uow: _FakeRepo(store)
    )
    capability = build_todo_capability(
        uow_factory=lambda: _FakeUoW(), conversation_id=uuid4()
    )
    toolset = capability.get_toolset()
    run_ctx = RunContext(
        deps=BaseAgentContext(
            user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()
        ),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    async def call(name: str, args: dict):
        prepared = await toolset.for_run(run_ctx)
        async with prepared:
            tools = await prepared.get_tools(run_ctx)
            tool = tools[name]
            validated = tool.args_validator.validate_python(
                args, context=run_ctx.validation_context
            )
            return await prepared.call_tool(name, validated, run_ctx, tool)

    # An empty list does not wipe the list; with no stored todos it returns an
    # empty list plus a guiding note (and persists nothing).
    empty = await call("write_todos", {"todos": []})
    assert empty["success"] is True
    assert empty["todos"] == [] and "No tasks provided" in empty["note"]
    assert "todos" not in store

    # An all-blank call is likewise a no-op and never persisted.
    blank = await call("write_todos", {"todos": ["   ", "", "- [ ]   "]})
    assert blank["success"] is True
    assert blank["todos"] == []
    assert "todos" not in store

    # A blank line mixed with a real one persists only the real task.
    mixed = await call("write_todos", {"todos": ["", "do the real work"]})
    assert mixed["success"] is True
    assert mixed["todos"] == ["- [ ] do the real work"]


@pytest.mark.anyio
async def test_current_time_and_deferral_in_real_run():
    visible = FunctionToolset[object]()

    @visible.tool_plain
    def visible_tool() -> str:
        return "ok"

    extra = FunctionToolset[object]()

    @extra.tool_plain
    def hidden_tool() -> str:
        return "secret"

    captured: dict[str, object] = {}

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        captured["defer"] = {
            tool.name: bool(getattr(tool, "defer_loading", False))
            for tool in info.function_tools
        }
        captured["settings"] = dict(info.model_settings or {})
        parts = messages[-1].parts
        captured["last_text"] = " ".join(getattr(part, "content", "") for part in parts)
        return ModelResponse(parts=[TextPart("done")])

    conversation_id = uuid4()
    agent = Agent(
        FunctionModel(model_fn),
        toolsets=[visible, extra.defer_loading()],
        capabilities=[
            CurrentTimeCapability(),
            PromptCachingCapability(conversation_id=conversation_id),
            ToolSearch(),
        ],
    )
    await agent.run("hello", deps=object())

    # The extra tool carries defer_loading=True so real provider adapters keep it
    # out of the prompt prefix until discovered; the core tool stays visible.
    assert captured["defer"]["visible_tool"] is False
    assert captured["defer"]["hidden_tool"] is True
    # Current time rides as the trailing (system) message, not the system prompt.
    assert "Current date and time:" in captured["last_text"]
    # Prompt-cache session affinity is applied to the request settings.
    assert captured["settings"].get("openai_user") == str(conversation_id)


# The 12 user-facing function tools (view_image is a separate, vision-gated
# toolset appended by the runner, not part of an agent's configured toolsets —
# see test_pod_default_gains_view_image_toolset_when_vision_supported).
# ToolSearch adds a 13th tool (`search_tools`) at the discovery layer for the
# live provider adapter; it isn't reported in FunctionModel's
# AgentInfo.function_tools, so it's asserted separately below.
_EXPECTED_VISIBLE_POD_DEFAULT_TOOLS = {
    "exec_command",
    "manage_process",
    "execute_python",
    "web_search",
    "list_skills",
    "load_skill",
    "display_resource",
    "request_approval",
    "ask_user",
    "write_todos",
    "say",
    "listen",
}


@pytest.mark.anyio
async def test_pod_default_visible_toolset_is_slim(monkeypatch):
    """The pod-default agent must expose only the slim visible set (13 function
    tools + search_tools = 14); POD and subagents are deferred behind search_tools."""
    from app.modules.agent.capabilities import todo_storage as storage_mod
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling
    from app.modules.agent.tools.context import BaseAgentContext
    from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS
    from app.modules.agent.tools.tool_assembler import RunToolAssembler

    monkeypatch.setattr(
        storage_mod, "ConversationRepository", lambda _uow: _FakeRepo({})
    )

    deps = BaseAgentContext(
        user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4(),
        is_pod_default_agent=True,
    )
    # Assemble through the real RunToolAssembler so the pod-default toolset
    # (including the conversation-scoped TODO toolset) is resolved exactly as in a
    # live run.
    full_toolsets = await RunToolAssembler(lambda: _FakeUoW()).assemble(
        agent=None,
        conversation=SimpleNamespace(id=deps.conversation_id, metadata={}),
    )
    capabilities = await build_lemma_harness_tooling(
        uow_factory=lambda: _FakeUoW(),
        agent=SimpleNamespace(toolsets=list(POD_DEFAULT_AGENT_TOOLSETS)),
        ctx=deps,
        full_toolsets=full_toolsets,
        agent_run_id=uuid4(),
        model_name="m",
        enable_prompt_caching=False,
    )

    captured: dict = {}

    def model_fn(messages, info: AgentInfo):
        captured["visible"] = {
            t.name for t in info.function_tools if not t.defer_loading
        }
        captured["deferred"] = {
            t.name for t in info.function_tools if t.defer_loading
        }
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model_fn), capabilities=capabilities)
    await agent.run("hi", deps=deps)

    assert captured["visible"] == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS
    # ToolSearch is wired (provides search_tools — the 14th visible tool live).
    assert any(isinstance(c, ToolSearch) for c in capabilities)
    # Subagents + POD are deferred, not in the visible prefix.
    assert {"spawn_subagent", "interact_subagent", "query_subagents"} <= captured[
        "deferred"
    ]
    assert any(name.startswith("pod_") for name in captured["deferred"])
    # A static awareness hint tells the model the deferred tools exist + how to
    # reach them — names listed, schemas not (so the model can search for them).
    from app.modules.agent.capabilities.deferred_hint import (
        DeferredToolsHintCapability,
    )

    hint_caps = [c for c in capabilities if isinstance(c, DeferredToolsHintCapability)]
    assert len(hint_caps) == 1
    hint = hint_caps[0].get_instructions()
    assert "search_tools" in hint
    assert "pod_tables" in hint and "spawn_subagent" in hint
    # Speech is a visible toolset (not deferred) and is not advertised in the hint.
    assert "say" not in captured["deferred"] and "listen" not in captured["deferred"]
    assert "Speech" not in hint


@pytest.mark.anyio
async def test_pod_default_speech_capability_carries_its_prompt(monkeypatch):
    """SPEECH is a visible capability whose instructions are the speech.md fragment,
    so every speech-enabled agent gets the spoken-reply/transcription guidance."""
    from app.modules.agent.capabilities import todo_storage as storage_mod
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling
    from app.modules.agent.capabilities.instructed_toolset import (
        InstructedToolsetCapability,
    )
    from app.modules.agent.tools.context import BaseAgentContext
    from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS
    from app.modules.agent.tools.tool_assembler import RunToolAssembler

    monkeypatch.setattr(
        storage_mod, "ConversationRepository", lambda _uow: _FakeRepo({})
    )

    deps = BaseAgentContext(
        user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4(),
        is_pod_default_agent=True,
    )
    full_toolsets = await RunToolAssembler(lambda: _FakeUoW()).assemble(
        agent=None,
        conversation=SimpleNamespace(id=deps.conversation_id, metadata={}),
    )
    capabilities = await build_lemma_harness_tooling(
        uow_factory=lambda: _FakeUoW(),
        agent=SimpleNamespace(toolsets=list(POD_DEFAULT_AGENT_TOOLSETS)),
        ctx=deps,
        full_toolsets=full_toolsets,
        agent_run_id=uuid4(),
        model_name="m",
        enable_prompt_caching=False,
    )

    speech_caps = [
        c
        for c in capabilities
        if isinstance(c, InstructedToolsetCapability)
        and c.get_serialization_name() == "speech"
    ]
    assert len(speech_caps) == 1
    instructions = speech_caps[0].get_instructions()
    assert "Spoken replies" in instructions
    assert "Do not also write the same words" in instructions
    assert "rewrite the transcript back to the user" in instructions


@pytest.mark.anyio
async def test_pod_default_gains_view_image_toolset_when_vision_supported():
    """`view_image` is not part of workspace_cli or any configured toolset — the
    runner appends the standalone `view_image_toolset` only when the resolved
    model supports vision (mirroring the vision-gated append in
    `agent_runner_service.py`)."""
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling
    from app.modules.agent.capabilities.instructed_toolset import (
        InstructedToolsetCapability,
    )
    from app.modules.agent.tools.context import BaseAgentContext
    from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS
    from app.modules.agent.tools.tool_assembler import RunToolAssembler
    from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
        view_image_toolset,
    )

    deps = BaseAgentContext(
        user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4(),
        is_pod_default_agent=True,
    )
    full_toolsets = await RunToolAssembler(lambda: _FakeUoW()).assemble(
        agent=None,
        conversation=SimpleNamespace(id=deps.conversation_id, metadata={}),
    )

    async def build_capabilities(*, supports_vision: bool) -> list[object]:
        toolsets = full_toolsets
        if supports_vision:
            toolsets = [*full_toolsets, view_image_toolset]
        return await build_lemma_harness_tooling(
            uow_factory=lambda: _FakeUoW(),
            agent=SimpleNamespace(toolsets=list(POD_DEFAULT_AGENT_TOOLSETS)),
            ctx=deps,
            full_toolsets=toolsets,
            agent_run_id=uuid4(),
            model_name="m",
            enable_prompt_caching=False,
        )

    async def visible_tool_names(capabilities: list[object]) -> set[str]:
        captured: dict = {}

        def model_fn(messages, info: AgentInfo):
            captured["visible"] = {
                t.name for t in info.function_tools if not t.defer_loading
            }
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(FunctionModel(model_fn), capabilities=capabilities)
        await agent.run("hi", deps=deps)
        return captured["visible"]

    # No vision → view_image absent; everything else is unaffected.
    no_vision_caps = await build_capabilities(supports_vision=False)
    assert (
        await visible_tool_names(no_vision_caps) == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS
    )

    # Vision supported → view_image appended as a plain core toolset capability
    # (no bespoke instructions needed; the tool's own docstring carries guidance).
    vision_caps = await build_capabilities(supports_vision=True)
    assert (
        await visible_tool_names(vision_caps)
        == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS | {"view_image"}
    )
    # The workspace_cli usage instructions still ride along, unaffected by
    # whether view_image is present.
    assert any(
        isinstance(c, InstructedToolsetCapability)
        and c.get_serialization_name() == "workspace_cli"
        for c in vision_caps
    )
