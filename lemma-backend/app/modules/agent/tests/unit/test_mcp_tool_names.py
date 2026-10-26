"""One tool, one name, however the agent calling it spells that name.

Every agent namespaces MCP tools with the server they came from, and each does
it differently. The namespace is collision avoidance and nothing else, so a
reader — a chat card, an icon, an approval, a usage record — has to see the same
`exec_command` whether the call came from a pod agent or from Claude Code
running on someone's laptop.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.entities import Agent
from app.modules.agent.infrastructure.mcp import (
    LEMMA_MCP_SERVER_NAME,
    MAX_LOCAL_TOOL_NAME_LENGTH,
    exported_tool_name,
    normalize_local_mcp_tool_name,
    resource_tool_name,
)
from app.modules.agent.infrastructure.tools.callables import (
    AgentCallableToolFactory,
    distinct_tool_names,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.dispatcher import AgentToolDispatcher

# What Claude and OpenAI accept as a tool name.
_PROVIDER_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _as_claude_code_sees_it(local_name: str) -> str:
    return f"mcp__{LEMMA_MCP_SERVER_NAME}__{exported_tool_name(local_name)}"


class TestNamespacesLemmaOwns:
    @pytest.mark.parametrize(
        "reported",
        [
            "mcp__lemma_tools__lemma_exec_command",
            "mcp.lemma_tools.lemma_exec_command",
            "lemma_tools.lemma_exec_command",
            "lemma_tools_lemma_exec_command",
            # Names older builds produced: a hyphenated server, and the Agent
            # Host's own `lemma` before it registered the run's published name.
            "mcp__lemma-tools__lemma_exec_command",
            "mcp__lemma__lemma_exec_command",
            "lemma__lemma_exec_command",
            # Already canonical, with and without the tool prefix.
            "lemma_exec_command",
            "exec_command",
        ],
    )
    def test_every_spelling_is_the_same_tool(self, reported: str) -> None:
        assert normalize_local_mcp_tool_name(reported) == "exec_command"

    def test_the_published_server_name_round_trips(self) -> None:
        """What a run publishes and what a name normalizes from are one thing."""
        wire_name = f"mcp__{LEMMA_MCP_SERVER_NAME}__{exported_tool_name('read_table')}"

        assert normalize_local_mcp_tool_name(wire_name) == "read_table"


class TestNamespacesLemmaDoesNotOwn:
    def test_a_third_party_mcp_tool_keeps_its_name(self) -> None:
        assert (
            normalize_local_mcp_tool_name("mcp__github__create_issue")
            == "mcp__github__create_issue"
        )

    def test_someone_elses_server_named_after_us_is_still_theirs(self) -> None:
        """The server name is matched whole. `lemma-corp` is not `lemma`."""
        assert (
            normalize_local_mcp_tool_name("mcp__lemma-corp__delete_everything")
            == "mcp__lemma-corp__delete_everything"
        )
        assert (
            normalize_local_mcp_tool_name("lemma-corp.delete_everything")
            == "lemma-corp.delete_everything"
        )

    def test_a_word_starting_with_lemma_is_not_a_namespace(self) -> None:
        """The server name has to be followed by a separator. Without that check
        `lemmatize_text` reads as the `lemma` server's `tize_text`."""
        assert normalize_local_mcp_tool_name("lemmatize_text") == "lemmatize_text"

    def test_a_native_tool_is_left_alone(self) -> None:
        for native in ("WebSearch", "Bash", "read_file"):
            assert normalize_local_mcp_tool_name(native) == native


class TestResourceToolNames:
    """`agent_<name>` and `function_<name>`, for names that are free text."""

    def test_an_ordinary_name_is_unchanged(self) -> None:
        assert resource_tool_name("agent_", "researcher") == "agent_researcher"

    @pytest.mark.parametrize(
        "name",
        [
            "quarterly_revenue_reconciliation_assistant_for_finance",
            "x" * 255,
            "café.reviewer",
            "sales/emea (draft)",
            "名前",
        ],
    )
    def test_any_name_is_a_valid_tool_name_on_every_surface(self, name: str) -> None:
        local = resource_tool_name("agent_", name)

        assert _PROVIDER_TOOL_NAME.fullmatch(local)
        assert _PROVIDER_TOOL_NAME.fullmatch(_as_claude_code_sees_it(local))
        assert local.startswith("agent_")
        assert resource_tool_name("agent_", name) == local

    def test_names_that_sanitize_alike_stay_two_tools(self) -> None:
        names = {
            resource_tool_name("agent_", "sales.emea"),
            resource_tool_name("agent_", "sales emea"),
            resource_tool_name("agent_", "sales_emea"),
            resource_tool_name("agent_", "a" * 60 + "1"),
            resource_tool_name("agent_", "a" * 60 + "2"),
        }

        assert len(names) == 5


async def test_a_long_agent_name_is_listed_and_called_by_the_same_tool_name() -> None:
    """Both harnesses look a tool up by the name it was built with, so a
    bounded name is still the one a call is dispatched on."""
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    pod_id, user_id = uuid4(), uuid4()
    child = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="quarterly_revenue_reconciliation_assistant_for_finance",
        instruction="Reconcile.",
    )
    parent = Agent(pod_id=pod_id, user_id=user_id, name="lead", instruction="Lead.")
    tool = AgentCallableToolFactory(uow_factory)._build_agent_tool(
        child, parent_agent=parent
    )
    toolset = FunctionToolset[ToolContext](tools=[tool])
    # The child itself calling its own tool is refused before anything is
    # spawned, which is all the dispatch half needs: an unknown name would
    # be refused as unknown instead.
    ctx = ToolContext(
        user_id=user_id, pod_id=pod_id, conversation_id=uuid4(), agent_name=child.name
    )
    dispatcher = AgentToolDispatcher(uow_factory)

    [listed] = await dispatcher.list_tools(ctx=ctx, toolsets=[toolset])
    assert _PROVIDER_TOOL_NAME.fullmatch(_as_claude_code_sees_it(listed.name))
    with pytest.raises(RuntimeError, match="cannot call itself"):
        await dispatcher.call_tool(
            ctx=ctx,
            name=normalize_local_mcp_tool_name(_as_claude_code_sees_it(listed.name)),
            arguments={"input": "go"},
            toolsets=[toolset],
        )


def test_a_resource_named_like_anothers_sanitized_form_gets_its_own_tool():
    """`x.y` sanitizes to `agent_x_y_<digest>`; an agent literally named that
    keeps its name. One toolset refuses two tools of one name, which failed the
    whole run, so the later one takes a counter and both build."""
    from pydantic_ai import Tool

    dotted = resource_tool_name("agent_", "x.y")
    literal = resource_tool_name("agent_", dotted.removeprefix("agent_"))
    assert dotted == literal

    def answer() -> str:
        return "ok"

    tools = distinct_tool_names([Tool(answer, name=dotted), Tool(answer, name=literal)])
    names = [tool.name for tool in tools]
    assert names == [dotted, dotted + "_2"]
    assert all(len(name) <= MAX_LOCAL_TOOL_NAME_LENGTH for name in names)
    FunctionToolset(tools=tools)
