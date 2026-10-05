"""Every tool a run answering somebody outside the pod is offered, named one by one.

A toolset is a bundle, and trusting one is how ``web_fetch`` -- which opens the
conversation owner's sandbox -- reached strangers: it rides in WEB_SEARCH beside
``web_search``. These tests hold the line by name:

* the ratchet: every tool in every toolset a stranger's run keeps is classified,
  allowed or withheld, so a new one fails here until somebody decides;
* the assembled run: what the model is actually offered is a subset of the
  allow-list, ``web_fetch`` and the write tools nowhere in it;
* the gate and the dispatcher refuse an off-list tool however it is asked for;
* a workspace -- the owner's sandbox, host or files -- is never opened for one.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

from app.modules.agent.capabilities.outsider_gate import (
    WITHHELD_MESSAGE,
    OutsiderToolGateCapability,
)
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    OUTSIDER_TOOLSETS,
    OUTSIDERS,
    OutsiderRunRefused,
    refuse_owner_workspace,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools import registry
from app.modules.agent.tools.contact_tools import build_contact_toolset
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher, UnknownToolError
from app.modules.agent.tools.outsider_tools import (
    OUTSIDER_TOOL_NAMES,
    OUTSIDER_TOOLS_WITHHELD,
)
from app.modules.agent.tools.web.pydantic_adapter import web_search_toolset
from app.modules.agent.tools.workspace_cli.pydantic_adapter import (
    workspace_cli_toolset,
)
from app.modules.agent.tools.workspace_cli.workspace_cli import get_workspace_session

pytestmark = pytest.mark.unit


def _kept_tool_names() -> set[str]:
    names: set[str] = set()
    for toolset in OUTSIDER_TOOLSETS:
        static = registry._TOOLSET_BY_NAME.get(toolset)
        if static is not None:
            names |= set(static.tools)
    # Built per run rather than registered, and only on a contact's run.
    names |= set(build_contact_toolset(uow_factory=lambda: None).tools)
    return names


def test_the_kept_toolsets_are_exactly_these():
    """Widening this is a decision about strangers, not a side effect."""
    assert OUTSIDER_TOOLSETS == {
        AgentToolset.POD,
        AgentToolset.WEB_SEARCH,
        AgentToolset.MESSAGING,
    }


def test_every_tool_a_stranger_could_reach_is_classified():
    """A tool added to a kept toolset fails here until somebody decides on it."""
    unclassified = (
        _kept_tool_names() - OUTSIDER_TOOL_NAMES - set(OUTSIDER_TOOLS_WITHHELD)
    )
    assert not unclassified, (
        f"Classify {sorted(unclassified)} in tools/outsider_tools.py: allowed "
        "for a stranger's run, or withheld with the reason."
    )


def test_the_allow_list_names_only_tools_that_exist():
    """A stale name here would read as a decision nobody made."""
    assert OUTSIDER_TOOL_NAMES <= _kept_tool_names()
    assert not (OUTSIDER_TOOL_NAMES & set(OUTSIDER_TOOLS_WITHHELD))


def test_web_fetch_is_withheld_and_web_search_is_not():
    assert "web_fetch" in OUTSIDER_TOOLS_WITHHELD
    assert "web_search" in OUTSIDER_TOOL_NAMES
    for write in ("pod_write_record", "pod_write_file", "pod_edit_file"):
        assert write in OUTSIDER_TOOLS_WITHHELD


class _FakeUoW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def commit(self) -> None:
        pass


def _stranger_deps(**overrides) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        org_id=uuid4(),
        conversation_id=uuid4(),
        is_pod_default_agent=True,
        answers_outsider=True,
        **overrides,
    )


@pytest.mark.anyio
async def test_an_assembled_strangers_run_is_offered_only_the_allow_list(monkeypatch):
    """Through the real assemblers and the gate, as the harness builds a run."""
    from app.modules.agent.capabilities.assembler import build_lemma_harness_tooling
    from app.modules.agent.tools.tool_assembler import RunToolAssembler

    deps = _stranger_deps()
    conversation = SimpleNamespace(
        id=deps.conversation_id,
        metadata={AUDIENCE_KEY: OUTSIDERS},
        is_pod_assistant=True,
        type=None,
        parent_id=None,
    )
    full_toolsets = await RunToolAssembler(lambda: _FakeUoW()).assemble(
        agent=None, conversation=conversation
    )
    capabilities = await build_lemma_harness_tooling(
        ctx=deps, full_toolsets=full_toolsets, enable_prompt_caching=False
    )
    capabilities.append(OutsiderToolGateCapability())

    offered: set[str] = set()

    def model_fn(messages, info: AgentInfo):
        offered.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("done")])

    model = FunctionModel(
        model_fn, profile=ModelProfile(tool_deferral_mode="with_tool_search")
    )
    await Agent(model, capabilities=capabilities).run("hi", deps=deps)

    assert offered, "the run was offered nothing at all"
    assert offered <= OUTSIDER_TOOL_NAMES, sorted(offered - OUTSIDER_TOOL_NAMES)
    assert "web_fetch" not in offered
    assert "message_user" in offered


@pytest.mark.anyio
async def test_the_gate_drops_and_refuses_an_off_list_tool():
    gate = OutsiderToolGateCapability()
    defs = [
        SimpleNamespace(name="web_search"),
        SimpleNamespace(name="web_fetch"),
        SimpleNamespace(name="exec_command"),
    ]

    kept = await gate.prepare_tools(SimpleNamespace(), defs)

    assert [tool.name for tool in kept] == ["web_search"]

    async def must_not_run(_args):
        raise AssertionError("an off-list tool ran on a stranger's run")

    refused = await gate.wrap_tool_execute(
        SimpleNamespace(),
        call=SimpleNamespace(),
        tool_def=SimpleNamespace(name="web_fetch"),
        args={},
        handler=must_not_run,
    )
    assert refused == {"success": False, "error": WITHHELD_MESSAGE}


@pytest.mark.anyio
async def test_the_dispatcher_hides_off_list_tools_whatever_it_is_handed():
    """The MCP bridge and the approval executor pass toolsets in explicitly."""
    dispatcher = AgentToolDispatcher(lambda: _FakeUoW())
    deps = _stranger_deps()

    listed = await dispatcher.list_tools(
        ctx=deps, toolsets=[web_search_toolset, workspace_cli_toolset]
    )
    names = {tool["name"] if isinstance(tool, dict) else tool.name for tool in listed}

    assert "web_search" in names
    assert "web_fetch" not in names
    assert "exec_command" not in names
    with pytest.raises(UnknownToolError):
        await dispatcher.call_tool(
            ctx=deps,
            name="web_fetch",
            arguments={"urls": ["https://example.com"]},
            toolsets=[web_search_toolset],
        )


@pytest.mark.anyio
async def test_no_workspace_is_opened_for_a_strangers_run():
    """The owner's sandbox: their files, env, a token minted for them."""

    class _Runtime:
        async def get_session(self, **_kwargs):
            raise AssertionError("the owner's sandbox was opened for a stranger")

        get_host_session = get_session

    with pytest.raises(OutsiderRunRefused):
        await get_workspace_session(
            _stranger_deps(), session_id=None, close_on_exit=True, runtime=_Runtime()
        )
    with pytest.raises(OutsiderRunRefused):
        _stranger_deps().file_manager  # noqa: B018 - the property is the test


def test_a_members_run_reaches_its_workspace_as_before():
    refuse_owner_workspace(SimpleNamespace(answers_outsider=False))
    refuse_owner_workspace(SimpleNamespace())
