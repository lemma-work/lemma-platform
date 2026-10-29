"""Unit tests for the in-process harness's capabilities.

Covers: what is visible and what is deferred, the guidance each toolset carries,
where the run's context part sits, prompt-cache levers and affinity, the todo
tool, and the deferred-tools hint -- all without a live stack.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import (
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent as AgentEntity
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.prompt.deferred_tools import deferred_tools_hint
from app.modules.agent.domain.prompt.templates import (
    FRAGMENT_BY_TOOLSET,
    load_memory_prompt,
    toolset_instructions,
)
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.domain.tools import ToolGroup, ToolSpec
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.infrastructure.harness.lemma.capabilities import (
    InstructedToolset,
    Instructions,
    PromptCaching,
    harness_capabilities,
)
from app.modules.agent.infrastructure.harness.lemma.history_parts import (
    RunContextCapability,
)
from app.modules.agent.infrastructure.harness.lemma.toolset_adapter import (
    function_toolset,
)
from app.modules.agent.services.tool_catalog import (
    STATIC_SPECS,
    ToolSurface,
    specs_for,
)
from app.modules.agent.services.tool_runtime import ToolRuntime


def _deferring_model(model_fn) -> FunctionModel:
    """A stand-in model that honours deferred tools like a real provider does.

    pydantic-ai only puts `defer_loading` on the wire when the model profile
    declares a deferral mode; both models Lemma runs resolve to
    ``'with_tool_search'``, so that is what these tests assert against.
    """
    return FunctionModel(
        model_fn, profile=ModelProfile(tool_deferral_mode="with_tool_search")
    )


def _deps(*, is_pod_default_agent: bool = True, **fields: object) -> ToolContext:
    return ToolContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
        is_pod_default_agent=is_pod_default_agent,
        **fields,
    )


def _capabilities(
    deps: ToolContext, specs: list[ToolSpec], **options: object
) -> list[object]:
    return harness_capabilities(
        ctx=deps,
        specs=specs,
        runtime=ToolRuntime(specs),
        enable_prompt_caching=bool(options.pop("caching", False)),
        **options,
    )


async def _pod_default_specs(deps: ToolContext) -> list[ToolSpec]:
    """The pod default assistant's tools, through the real catalog."""
    return await specs_for(
        agent=None,
        conversation=Conversation(
            id=deps.conversation_id, user_id=deps.user_id, pod_id=deps.pod_id
        ),
        ctx=deps,
        surface=ToolSurface.IN_PROCESS,
        uow_factory=_FakeUoW,
    )


async def _offered(
    capabilities: list[object], deps: ToolContext
) -> tuple[set[str], set[str]]:
    """``(visible, deferred)`` tool names, as the model's first request has them."""
    seen: dict[str, set[str]] = {}

    def model_fn(messages, info: AgentInfo):
        seen["visible"] = {t.name for t in info.function_tools if not t.defer_loading}
        seen["deferred"] = {t.name for t in info.function_tools if t.defer_loading}
        return ModelResponse(parts=[TextPart("done")])

    await Agent(_deferring_model(model_fn), capabilities=capabilities).run(
        "hi", deps=deps
    )
    return seen["visible"], seen["deferred"]


def _guidance_carrying(capabilities: list[object], tool_name: str) -> list[str]:
    """The guidance of each capability whose toolset holds ``tool_name``."""
    found = []
    for capability in capabilities:
        if not isinstance(capability, InstructedToolset):
            continue
        toolset = capability.get_toolset()
        tools = getattr(getattr(toolset, "wrapped", toolset), "tools", {})
        if tool_name in tools:
            found.append(capability.get_instructions())
    return found


def _hint(capabilities: list[object]) -> list[str]:
    return [
        c.get_instructions()
        for c in capabilities
        if type(c) is Instructions
        and c.get_instructions().startswith("# Tools available on demand")
    ]


def _deferrable_groups() -> list[list[ToolSpec]]:
    groups: dict[ToolGroup, list[ToolSpec]] = {}
    for specs in STATIC_SPECS.values():
        for spec in specs:
            if spec.policy.deferrable:
                groups.setdefault(spec.toolset, []).append(spec)
    return list(groups.values())


@pytest.mark.anyio
async def test_the_pod_tools_are_visible_rather_than_deferred():
    """The pod's own data is not an "extra" the agent should have to find.

    They were deferred, and then went almost unused while the shell rebuilt the
    same operations through the CLI. Deferral is right for surfaces that are
    large (connectors), niche (browser) or deliberately harder to reach
    (messaging) — not for the thing the pod is.
    """
    deps = _deps()
    specs = [
        *STATIC_SPECS[AgentToolset.WORKSPACE_CLI],
        *STATIC_SPECS[AgentToolset.POD],
        *STATIC_SPECS[AgentToolset.WEB_SEARCH],
    ]
    visible, deferred = await _offered(_capabilities(deps, specs), deps)
    assert {"pod_tables", "exec_command", "web_search"} <= visible
    assert not deferred


@pytest.mark.anyio
async def test_a_user_created_agent_keeps_everything_visible():
    """User-created agents that configured POD/SUBAGENTS get them injected
    directly (no ToolSearch round-trip) — only the pod-default agent defers."""
    deps = _deps(is_pod_default_agent=False)
    specs = [
        *STATIC_SPECS[AgentToolset.POD],
        *STATIC_SPECS[AgentToolset.SUBAGENTS],
        *STATIC_SPECS[AgentToolset.MESSAGING],
    ]
    capabilities = _capabilities(deps, specs)
    visible, deferred = await _offered(capabilities, deps)
    assert not deferred
    assert {"spawn_subagent", "channel_post", "list_pod_members"} <= visible
    assert not any(isinstance(c, ToolSearch) for c in capabilities)


def test_prompt_caching_keys_on_conversation_id():
    conversation_id = uuid4()
    settings = PromptCaching(conversation_id=conversation_id).get_model_settings()
    assert settings["openai_user"] == str(conversation_id)
    # `prompt_cache_key` is OpenAI-only and a strict compat shim 400s on it, and
    # the protocol names a wire format, not a vendor: no provider headers.
    assert "openai_prompt_cache_key" not in settings
    assert "extra_headers" not in settings


def test_openai_compatible_settings_stay_inside_the_baseline_schema():
    """A strict compat shim (Google's) 400s on a field it does not know, so
    nothing outside the baseline Chat Completions schema is sent."""
    settings = PromptCaching(conversation_id=uuid4()).get_model_settings()

    assert set(settings) == {"openai_user"}


def test_web_search_carries_its_guidance_beside_its_tools():
    deps = _deps(is_pod_default_agent=False)
    capabilities = _capabilities(deps, list(STATIC_SPECS[AgentToolset.WEB_SEARCH]))
    [guidance] = _guidance_carrying(capabilities, "web_search")
    assert "Web research" in guidance


def test_showing_your_work_is_taught_on_both_paths():
    """`display_resource` needs prose, not only a tool schema.

    A coding agent that meets it as one MCP tool underneath its own system
    prompt answered in prose and never displayed anything, so both the
    in-process and the remote-harness paths carry the fragment.
    """
    in_process = toolset_instructions(ToolGroup.USER_INTERACTION)
    assert in_process is not None, "the in-process path lost the fragment"
    assert "display_resource" in in_process

    remote = FRAGMENT_BY_TOOLSET.get(AgentToolset.USER_INTERACTION)
    assert remote is not None, "the remote-harness path lost the fragment"
    assert "display_resource" in remote.read_text()


def test_independent_calls_are_invited_into_one_turn_on_both_paths():
    """The reply rules are the one prompt every run gets on both harnesses, so
    that is where the invitation to call independent tools in parallel lives."""
    from app.modules.agent.domain.prompt.templates import build_agent_instructions

    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = AgentEntity(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="reader",
        instruction="",
    )
    for include_fragments in (True, False):
        prompt = build_agent_instructions(
            agent=agent,
            conversation=conversation,
            ctx=SimpleNamespace(run_toolsets=[]),
            include_toolset_prompts=include_fragments,
        )
        assert "as parallel tool calls" in " ".join(prompt.split())


def test_the_workspace_contract_follows_the_machine_the_commands_run_on():
    """A run whose `exec_command` is on the user's Mac is told about the Mac."""
    vm = toolset_instructions(ToolGroup.WORKSPACE_CLI)
    mac = toolset_instructions(ToolGroup.WORKSPACE_CLI, host_execution=True)
    assert vm is not None and mac is not None
    assert "whole home directory persists" in vm
    assert "on the user's own Mac" in mac


def test_the_memory_contract_is_taught_on_both_paths():
    """Memory has no toolset to hang its fragment on, so it rides on its own
    in process -- and this notices if only one of the two paths keeps it."""
    in_process = load_memory_prompt()
    assert "/memory" in in_process and "AGENTS.md" in in_process

    remote = FRAGMENT_BY_TOOLSET.get(AgentToolset.MEMORY)
    assert remote is not None, "the remote-harness path lost the fragment"
    assert remote.read_text().strip() == in_process


def test_the_memory_contract_is_written_down_exactly_once():
    """It used to be three hand-synced copies: this fragment, and paragraphs in
    the `pod_write_file` and `pod_read_file` docstrings."""
    from app.modules.agent.domain.prompt.templates import load_workspace_cli_prompt
    from app.modules.agent.infrastructure.tools.pod.pod_file_tools import (
        pod_read_file,
        pod_write_file,
    )

    for docstring in (pod_write_file.__doc__ or "", pod_read_file.__doc__ or ""):
        assert "AGENTS.md" not in docstring
        assert "/memory" not in docstring
    assert "## Memory" not in load_workspace_cli_prompt()


def test_the_memory_contract_is_appended_only_when_memory_is_enabled():
    def has_memory(capabilities: list[object]) -> bool:
        return any(
            type(c) is Instructions and c.get_instructions() == load_memory_prompt()
            for c in capabilities
        )

    assert not has_memory(_capabilities(_deps(memory_enabled=False), []))
    assert has_memory(_capabilities(_deps(memory_enabled=True), []))


def test_every_visible_toolset_is_a_capability_with_its_guidance():
    deps = _deps()
    specs = [
        *STATIC_SPECS[AgentToolset.WEB_SEARCH],
        *STATIC_SPECS[AgentToolset.WORKSPACE_CLI],
    ]
    capabilities = _capabilities(deps, specs, caching=True)

    assert _guidance_carrying(capabilities, "web_search")
    assert _guidance_carrying(capabilities, "exec_command")
    assert any(isinstance(c, RunContextCapability) for c in capabilities)
    assert any(isinstance(c, PromptCaching) for c in capabilities)
    # Nothing deferrable → no tool search.
    assert not any(isinstance(c, ToolSearch) for c in capabilities)


def test_caching_uses_the_lever_its_protocol_understands():
    """OpenAI gets session affinity; Anthropic gets explicit cache breakpoints,
    since it caches nothing without one."""

    def settings_for(protocol: RuntimeProfileProtocol) -> dict[str, object]:
        capabilities = _capabilities(
            _deps(),
            list(STATIC_SPECS[AgentToolset.WEB_SEARCH]),
            caching=True,
            protocol=protocol,
        )
        caching = next(c for c in capabilities if isinstance(c, PromptCaching))
        return caching.get_model_settings()

    openai_settings = settings_for(RuntimeProfileProtocol.OPENAI_COMPATIBLE)
    assert "openai_user" in openai_settings
    assert "anthropic_cache_instructions" not in openai_settings

    anthropic_settings = settings_for(RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE)
    assert anthropic_settings["anthropic_cache_instructions"] == "5m"
    # The history too: without a message-level breakpoint every step of a run
    # re-reads every message.
    assert anthropic_settings["anthropic_cache_messages"] == "5m"
    assert "openai_user" not in anthropic_settings


def test_affinity_is_keyed_on_the_pod_and_agent_a_real_run_carries():
    """Every conversation with one agent in one pod shares the long start of
    its prompt, so that is the key, not the conversation."""
    deps = _deps(agent_name="researcher")
    capabilities = _capabilities(
        deps, list(STATIC_SPECS[AgentToolset.WEB_SEARCH]), caching=True
    )
    caching = next(c for c in capabilities if isinstance(c, PromptCaching))

    assert caching.get_model_settings()["openai_user"] == f"{deps.pod_id}:researcher"


class _FakeRepo:
    """The conversation row's ``todos`` column, and one other column beside it."""

    def __init__(self, store: dict) -> None:
        self._store = store

    async def get_todos(self, _cid):
        return list(self._store.get("todos", []))

    async def set_todos(self, _cid, todos) -> None:
        self._store["todos"] = list(todos)


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

    from app.modules.agent.infrastructure.tools import todo as storage_mod
    from app.modules.agent.infrastructure.tools.todo import todo_specs
    from app.modules.agent.domain.context import ToolContext

    store: dict = {"cwd": "/w"}  # a sibling column
    monkeypatch.setattr(storage_mod, "Conversations", lambda _uow: _FakeRepo(store))

    specs = todo_specs(uow_factory=_FakeUoW)
    toolset = function_toolset(specs, ToolRuntime(specs))
    run_ctx = RunContext(
        deps=ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()),
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
    # The sibling column is untouched (todos live in their own).
    assert store["cwd"] == "/w"
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
    assert store["cwd"] == "/w"

    # Once the current plan is fully complete, new unchecked work starts a
    # fresh plan instead of retaining completed history forever.
    result = await call("write_todos", {"todos": ["- [x] ship it"]})
    assert all(line.startswith("- [x]") for line in result["todos"])
    result = await call("write_todos", {"todos": ["- [ ] start the next plan"]})
    assert result["todos"] == ["- [ ] start the next plan"]

    # Multiple lines are an authoritative snapshot, so renamed/replanned tasks
    # replace the old list rather than accumulating conversation-wide history.
    result = await call(
        "write_todos",
        {"todos": ["- [x] New research", "- [ ] New report"]},
    )
    assert result["todos"] == ["- [x] New research", "- [ ] New report"]

    # Some XML-oriented model/tool parsers flatten a string array into one value.
    # Recover the individual items and their inner checkbox state before storing.
    result = await call(
        "write_todos",
        {
            "todos": [
                (
                    "- [x] Research</td>\n"
                    "<item>- [x] Build deck</item></item>\n"
                    "<item>- [ ] Upload</item>\n</todos>"
                )
            ]
        },
    )
    assert result["todos"] == [
        "- [x] Research",
        "- [x] Build deck",
        "- [ ] Upload",
    ]

    # Status prose from the observed malformed payload is also normalized.
    result = await call(
        "write_todos",
        {
            "todos": [
                (
                    "RESEARCH DONE</item>\n"
                    "<item>DECK DONE</item>\n"
                    "<item>WRITE HTML DONE</item>\n"
                    "<item>RENDER PDF DONE</item>\n"
                    "<item>UPLOAD DONE"
                )
            ]
        },
    )
    assert result["todos"] == [
        "- [x] RESEARCH",
        "- [x] DECK",
        "- [x] WRITE HTML",
        "- [x] RENDER PDF",
        "- [x] UPLOAD",
    ]


@pytest.mark.anyio
async def test_write_todos_says_what_to_do_next_on_every_call(monkeypatch):
    """The usage prompt is many tool calls away by the time an item finishes.

    Writing a plan is the half models do reliably; checking items off is the
    half they drop, and the instruction to do it lived only in the system
    prompt. It rides back on the tool result now, where the model is already
    looking, naming the next item and when to flip it.
    """
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    from app.modules.agent.infrastructure.tools import todo as storage_mod
    from app.modules.agent.infrastructure.tools.todo import todo_specs
    from app.modules.agent.domain.context import ToolContext

    store: dict = {}
    monkeypatch.setattr(storage_mod, "Conversations", lambda _uow: _FakeRepo(store))
    specs = todo_specs(uow_factory=_FakeUoW)
    toolset = function_toolset(specs, ToolRuntime(specs))
    run_ctx = RunContext(
        deps=ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    async def call(args: dict):
        prepared = await toolset.for_run(run_ctx)
        async with prepared:
            tools = await prepared.get_tools(run_ctx)
            tool = tools["write_todos"]
            validated = tool.args_validator.validate_python(
                args, context=run_ctx.validation_context
            )
            return await prepared.call_tool("write_todos", validated, run_ctx, tool)

    planned = await call({"todos": ["- [ ] Fetch the Q3 report", "- [ ] Summarize"]})
    assert planned["next"] == "Fetch the Q3 report"
    assert "0 of 2 done" in planned["reminder"]
    # The exact call to make, in the task's own words, so flipping it is copying.
    assert "- [x] Fetch the Q3 report" in planned["reminder"]
    # Check off promptly -- but the list is no lock on the work: nothing says
    # to finish one item before touching the next.
    assert "as soon as it is finished" in planned["reminder"]
    assert "before starting" not in planned["reminder"]

    flipped = await call({"todos": ["- [x] Fetch the Q3 report"]})
    assert flipped["todos"] == ["- [x] Fetch the Q3 report", "- [ ] Summarize"]
    assert flipped["next"] == "Summarize"
    assert "1 of 2 done" in flipped["reminder"]

    finished = await call({"todos": ["- [x] Summarize"]})
    assert "next" not in finished
    assert "Every item is checked off" in finished["reminder"]


@pytest.mark.anyio
async def test_a_reworded_check_off_flips_the_task_instead_of_adding_one(monkeypatch):
    """Text is how a single line finds its task, and models paraphrase.

    "- [x] Fetch Q3 report" against a planned "Fetch the Q3 report" used to
    append a second, completed item: the planned one stayed open forever and
    the list grew a near-duplicate. Adding a genuinely new task must still add.
    """
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    from app.modules.agent.infrastructure.tools import todo as storage_mod
    from app.modules.agent.infrastructure.tools.todo import todo_specs
    from app.modules.agent.domain.context import ToolContext

    store: dict = {}
    monkeypatch.setattr(storage_mod, "Conversations", lambda _uow: _FakeRepo(store))
    specs = todo_specs(uow_factory=_FakeUoW)
    toolset = function_toolset(specs, ToolRuntime(specs))
    run_ctx = RunContext(
        deps=ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()),
        model=None,  # type: ignore[arg-type]
        usage=RunUsage(),
        prompt=None,
    )

    async def call(args: dict):
        prepared = await toolset.for_run(run_ctx)
        async with prepared:
            tools = await prepared.get_tools(run_ctx)
            tool = tools["write_todos"]
            validated = tool.args_validator.validate_python(
                args, context=run_ctx.validation_context
            )
            return await prepared.call_tool("write_todos", validated, run_ctx, tool)

    await call({"todos": ["- [ ] Fetch the Q3 report", "- [ ] Summarize findings"]})

    reworded = await call({"todos": ["- [x] Fetch Q3 report"]})

    # Flipped in place, and stored under the wording the person already saw.
    assert reworded["todos"] == [
        "- [x] Fetch the Q3 report",
        "- [ ] Summarize findings",
    ]

    # An unrelated new task is still an addition, not a mangled match.
    added = await call({"todos": ["- [ ] Email the board"]})
    assert added["todos"][-1] == "- [ ] Email the board"

    # An unchecked line that resembles an open task is left alone too: only a
    # check-off is treated as "I meant the one already on the list".
    restated = await call({"todos": ["- [ ] Summarise findings"]})
    assert "- [ ] Summarise findings" in restated["todos"]


def test_normalize_stored_todos_recovers_observed_corrupt_history():
    from app.modules.agent.infrastructure.tools.todo import _normalize_stored

    stored = [
        {
            "done": False,
            "content": (
                "[ ] Research Hermes Agent</td>\n"
                "<item>- [ ] Build deck</td>\n"
                "<item>- [ ] Upload</td>"
            ),
        },
        {
            "done": False,
            "content": (
                "[x] Research Hermes Agent</item>\n"
                "<item>- [x] Build deck</item>\n"
                "<item>- [ ] Upload</item>"
            ),
        },
        {"done": False, "content": "RESEARCH DONE"},
        {"done": False, "content": "DECK DONE"},
        {"done": False, "content": "UPLOAD"},
        {
            "done": False,
            "content": (
                "RESEARCH DONE</item>\n<item>DECK DONE</item>\n<item>UPLOAD DONE"
            ),
        },
    ]

    assert _normalize_stored(stored) == [
        {"content": "RESEARCH", "done": True},
        {"content": "DECK", "done": True},
        {"content": "UPLOAD", "done": True},
    ]


@pytest.mark.anyio
async def test_write_todos_guards_empty_and_blank_calls(monkeypatch):
    from pydantic_ai.tools import RunContext
    from pydantic_ai.usage import RunUsage

    from app.modules.agent.infrastructure.tools import todo as storage_mod
    from app.modules.agent.infrastructure.tools.todo import todo_specs
    from app.modules.agent.domain.context import ToolContext

    store: dict = {}
    monkeypatch.setattr(storage_mod, "Conversations", lambda _uow: _FakeRepo(store))
    specs = todo_specs(uow_factory=_FakeUoW)
    toolset = function_toolset(specs, ToolRuntime(specs))
    run_ctx = RunContext(
        deps=ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()),
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
async def test_run_context_and_deferral_in_real_run():
    visible = FunctionToolset[object]()

    @visible.tool_plain
    def visible_tool() -> str:
        return "ok"

    extra = FunctionToolset[object]()

    @extra.tool_plain
    def hidden_tool() -> str:
        return "secret"

    captured: dict[str, object] = {}
    turns: list[list[object]] = []

    def model_fn(messages, info: AgentInfo) -> ModelResponse:
        captured["defer"] = {
            tool.name: bool(getattr(tool, "defer_loading", False))
            for tool in info.function_tools
        }
        captured["settings"] = dict(info.model_settings or {})
        turns.append(list(messages))
        # First pass calls a tool, forcing a second model request in the same
        # run, so where the part goes on a later request is exercised.
        if len(turns) == 1:
            return ModelResponse(parts=[ToolCallPart("visible_tool", {})])
        return ModelResponse(parts=[TextPart("done")])

    context = "<run_context>\nthe run's context\n</run_context>"
    conversation_id = uuid4()
    agent = Agent(
        _deferring_model(model_fn),
        toolsets=[visible, extra.defer_loading()],
        capabilities=[
            RunContextCapability(context),
            PromptCaching(conversation_id=conversation_id),
            ToolSearch(),
        ],
    )
    await agent.run("hello", deps=object())

    # The extra tool carries defer_loading=True so real provider adapters keep it
    # out of the prompt prefix until discovered; the core tool stays visible.
    assert captured["defer"]["visible_tool"] is False
    assert captured["defer"]["hidden_tool"] is True

    def user_texts(messages: list[object]) -> list[str]:
        return [
            str(part.content)
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, UserPromptPart)
        ]

    # A USER part, never a SystemPromptPart: Anthropic hoists system parts to
    # the front of the system prompt, ahead of the whole cacheable prefix.
    assert not any(
        isinstance(part, SystemPromptPart) and "<run_context>" in str(part.content)
        for message in turns[0]
        for part in getattr(message, "parts", [])
    )
    # First request: just before the person's message, which stays last.
    assert user_texts(turns[0])[-2:] == [context, "hello"]

    # Later request: once, where the first request had it -- nothing already
    # sent moves, so the request starts with the whole of the one before.
    assert len(turns) == 2
    assert user_texts(turns[1]) == [context, "hello"]

    # Prompt-cache session affinity is applied to the request settings.
    assert captured["settings"].get("openai_user") == str(conversation_id)


_EXPECTED_VISIBLE_POD_DEFAULT_TOOLS = {
    "exec_command",
    "manage_process",
    "execute_python",
    "web_search",
    # Capturing a page into the workspace is core research, not an extra: the
    # alternative was a shell script the model had to be told about in prose.
    "web_fetch",
    "list_skills",
    "load_skill",
    "display_resource",
    "request_approval",
    "ask_user",
    # Always there, open question or not: see `tool_catalog._seen_and_placed`.
    "answer_question",
    "write_todos",
    # Delegation is one visible tool: behind search it went unused, and a
    # reveal rewrote the cached tool array (subagents.md).
    "delegate",
    # The pod's own data and files. Visible on purpose, and it is the one entry
    # here that costs real prefix budget: ten schemas in every pod-default
    # prompt. Deferred, they went almost unused — most conversations that
    # touched pod files did it through the `lemma` CLI in a shell, and paid for
    # it in CLI usage errors. A tool that has to be found first, competing with
    # a command the prompt already showed, loses.
    "pod_tables",
    "pod_get_records",
    "pod_write_record",
    "pod_query",
    "pod_list_files",
    "pod_read_file",
    "pod_write_file",
    "pod_edit_file",
    "pod_search_files",
    "pod_get_file_url",
    "pod_view_document_pages",
}


@pytest.mark.anyio
async def test_pod_default_visible_toolset_is_slim():
    """The pod-default agent's visible set is exactly this, and no wider.

    The set is a budget, not an inventory: everything in it is paid for in
    every prompt. Anything new here should have to make the argument the pod
    tools did.
    """
    deps = _deps()
    capabilities = _capabilities(deps, await _pod_default_specs(deps))
    visible, deferred = await _offered(capabilities, deps)

    assert visible == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS
    assert any(isinstance(c, ToolSearch) for c in capabilities)
    # `delegate` is in view; the rest of the sub-agent controls stay deferred.
    assert {"spawn_subagent", "interact_subagent", "query_subagents"} <= deferred
    assert not any(name.startswith("pod_") for name in deferred)
    # Asking for a login is the deliberate step past `web_fetch`.
    assert "browser_sign_in" in deferred
    # The hint names what is deferred -- with a line each -- and nothing else.
    [hint] = _hint(capabilities)
    assert "spawn_subagent" in hint
    assert "Deprecated: use `delegate`" in hint
    assert {"say", "listen", "list_voices"} <= deferred
    assert "Speaking and transcribing" in hint
    assert "pod_tables" not in hint


@pytest.mark.anyio
async def test_pod_default_speech_carries_its_prompt():
    """Speech is deferred on the pod default, and its guidance still rides."""
    deps = _deps()
    capabilities = _capabilities(deps, await _pod_default_specs(deps))

    [instructions] = _guidance_carrying(capabilities, "say")
    assert "Spoken replies" in instructions
    assert "add text only for different information" in instructions
    assert "without echoing them" in instructions


@pytest.mark.anyio
async def test_pod_default_gains_view_image_when_vision_is_supported():
    """`view_image` is offered whenever the run's model can interpret an image,
    whatever toolsets were configured."""
    from app.modules.agent.domain.vision import AgentVisionMode

    blind, seeing = _deps(), _deps(vision_mode=AgentVisionMode.DIRECT)

    no_vision, _ = await _offered(
        _capabilities(blind, await _pod_default_specs(blind)), blind
    )
    assert no_vision == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS

    vision_caps = _capabilities(seeing, await _pod_default_specs(seeing))
    vision, _ = await _offered(vision_caps, seeing)
    assert vision == _EXPECTED_VISIBLE_POD_DEFAULT_TOOLS | {"view_image"}
    assert _guidance_carrying(vision_caps, "exec_command")


@pytest.mark.anyio
async def test_pod_default_messaging_is_deferred_but_keeps_its_contract():
    """Deferral hides a toolset's schemas. It must not hide what the tool means:
    `channel_post` returns at once and the reply never comes back as its result."""
    deps = _deps()
    capabilities = _capabilities(deps, await _pod_default_specs(deps))
    visible, deferred = await _offered(capabilities, deps)

    assert {"channel_post", "channel_read", "list_pod_members", "wait_for"} <= deferred
    assert not ({"channel_post", "wait_for"} & visible)
    [instructions] = _guidance_carrying(capabilities, "channel_post")
    assert "wait_for" in instructions
    assert "channel_read(asked_by_me=True)" in instructions


@pytest.mark.anyio
async def test_pod_is_visible_and_names_itself_the_default_for_pod_data():
    """The pod toolset is in the prefix, and still says it beats the CLI there:
    visible schemas say what each tool does, not which of two working paths to
    prefer."""
    deps = _deps()
    capabilities = _capabilities(deps, await _pod_default_specs(deps))
    visible, deferred = await _offered(capabilities, deps)

    assert {"pod_query", "pod_read_file", "pod_tables"} <= visible
    assert not any(name.startswith("pod_") for name in deferred)

    [instructions] = _guidance_carrying(capabilities, "pod_query")
    assert "not the `lemma` CLI" in instructions
    assert "pod_query" in instructions and "pod_read_file" in instructions
    # The reason to build code in the workspace is the editing loop, not the
    # destination: a pod write replaces the file, the workspace applies a diff.
    assert "applies a diff" in instructions
    assert "replaces the file" in instructions
    assert "lemma pods import" in instructions
    assert "not the destination" in instructions
    for scary in ("does not go in pod files", "slips through", "Nothing warns you"):
        assert scary not in instructions, (
            f"fragment discourages a valid write: {scary!r}"
        )

    # And the CLI fragment must not still teach the operations it lost.
    from app.modules.agent.domain.prompt.templates import load_workspace_cli_prompt

    cli = load_workspace_cli_prompt()
    for duplicated in (
        "lemma tables list",
        "lemma records list",
        "lemma query run",
        "lemma files ls",
        "lemma files cat",
        "lemma files write",
        "lemma files search",
    ):
        assert duplicated not in cli, f"{duplicated!r} competes with a pod_* tool"


def test_every_deferred_group_is_labelled():
    """An unlabelled group renders as "Additional tools", which says nothing."""
    hint = deferred_tools_hint(_deferrable_groups())

    assert hint is not None
    assert "Additional tools" not in hint
    # The member lookup has to be discoverable, or channel_post stays unusable
    # for anyone the agent only knows by name.
    assert "list_pod_members" in hint


def test_the_deferred_hint_is_byte_stable_across_builds():
    """This block rides in the cached prompt prefix: an order that came from set
    iteration would invalidate the cache on every request."""
    assert deferred_tools_hint(_deferrable_groups()) == deferred_tools_hint(
        _deferrable_groups()
    )


def test_tool_names_are_sorted_within_each_group():
    """Group order follows the caller's list; names within a group must not."""
    from app.modules.agent.domain.prompt.deferred_tools import _tool_summaries

    names = [name for name, _summary in _tool_summaries(STATIC_SPECS[AgentToolset.POD])]
    assert names == sorted(names)


def test_every_deferred_tool_carries_a_description():
    """A name with no summary is the failure mode this replaced."""
    from app.modules.agent.domain.prompt.deferred_tools import _tool_summaries

    for group in _deferrable_groups():
        for name, summary in _tool_summaries(group):
            assert summary, f"{name} has no description to show the model"
            assert len(summary) <= 100, f"{name} summary is too long: {summary!r}"
            assert "\n" not in summary


def test_the_hint_stays_within_a_token_budget():
    """Only worth carrying while it stays a rounding error against the schemas."""
    from app.modules.agent.infrastructure.providers.tokens import count_text_tokens

    hint = deferred_tools_hint(_deferrable_groups())
    assert hint is not None and count_text_tokens(hint) < 900


def test_the_hint_does_not_name_a_provider_specific_search_tool():
    """`search_tools` is only the local fallback: on Anthropic and OpenAI the
    reveal is provider-native and no such tool exists to call."""
    hint = deferred_tools_hint(_deferrable_groups())
    assert hint is not None and "search_tools" not in hint
