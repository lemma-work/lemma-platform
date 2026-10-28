from __future__ import annotations

import json

from uuid import uuid4

import pytest

from app.modules.agent.domain.state_kinds import PauseKind
from pydantic import BaseModel

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.agent_host.mcp import ConversationMCPService
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ToolGroup, tool
from app.modules.agent.domain.toolsets import AgentGrantSummary
from app.modules.agent.domain.value_objects import ConversationOrigin


class _SurfaceToolRequest(BaseModel):
    path: str


@pytest.mark.asyncio
async def test_conversation_mcp_includes_surface_toolsets(monkeypatch):
    async def telegram_send_file(
        ctx: ToolContext, request: _SurfaceToolRequest
    ) -> dict[str, str]:
        """Send a file to the current Telegram chat."""
        return {"conversation_id": str(ctx.conversation_id), "path": request.path}

    user_id = uuid4()
    pod_id = uuid4()
    conversation_id = uuid4()
    agent = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="Surface MCP Agent",
        instruction="Use surface tools.",
    )
    conversation = Conversation(
        id=conversation_id,
        user_id=user_id,
        pod_id=pod_id,
        title="Telegram chat",
    )
    ctx = ToolContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_name=agent.name,
        surface_platform="TELEGRAM",
        external_channel_id="12345",
        external_thread_id="12345",
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx, AgentGrantSummary()

    async def fake_build_tools(self, **place):
        del self, place
        return [tool(telegram_send_file, ToolGroup.SURFACE)]

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True, grants=None):
        # Unit test: must not touch the real database. The production
        # implementation runs a SQL query whenever the agent has an id (always
        # true, ids are auto-generated).
        del self, agent, allow_subagents, grants
        return []

    monkeypatch.setattr(
        ConversationMCPService,
        "_load_agent_context",
        fake_load_agent_context,
    )
    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.callables."
        "AgentCallableToolFactory.build_specs",
        fake_callable_toolsets,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory."
        "SurfacePlatformToolFactory.build_tools",
        fake_build_tools,
    )

    service = ConversationMCPService()
    tools = await service.list_tools(conversation_id=conversation_id)

    exported = {tool.name: tool for tool in tools}
    assert "lemma_telegram_send_file" in exported
    assert (
        exported["lemma_telegram_send_file"].description
        == "Send a file to the current Telegram chat."
    )


@pytest.mark.asyncio
async def test_conversation_mcp_exposes_todo_tools_when_agent_has_todo(monkeypatch):
    # The remote harnesses (Codex/Claude-Code/OpenCode) reach tools through this
    # exact path, so an agent whose toolsets include TODO must get the todo tools
    # over MCP too (the in-process LEMMA harness isn't the only consumer).
    from app.modules.agent.domain.value_objects import AgentToolset

    user_id = uuid4()
    pod_id = uuid4()
    conversation_id = uuid4()
    agent = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="Planner",
        instruction="Plan tasks.",
        toolsets=[AgentToolset.TODO],
    )
    conversation = Conversation(
        id=conversation_id, user_id=user_id, pod_id=pod_id, title="Planning"
    )
    ctx = ToolContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_name=agent.name,
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx, AgentGrantSummary()

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True, grants=None):
        del self, agent, allow_subagents, grants
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.callables."
        "AgentCallableToolFactory.build_specs",
        fake_callable_toolsets,
    )

    service = ConversationMCPService()
    names = {
        tool.name for tool in await service.list_tools(conversation_id=conversation_id)
    }
    assert "lemma_write_todos" in names
    # The todo surface is a single merge-by-text tool now (no status updater).
    assert "lemma_update_todo_status" not in names


@pytest.mark.asyncio
async def test_a_pausing_tool_reaches_a_person_over_mcp(monkeypatch):
    """End to end through the real `ask_user`, on the real MCP route.

    Every pausing tool addresses itself by ``ctx.tool_call_id``: it is the id an
    approval card is answered through, the id a decision is recorded against,
    and the id a wait's wake writes its return under. Nothing on the MCP wire
    supplies one — ``tools/call`` carries a name and arguments, and the
    JSON-RPC id dies with the response — so it arrived as ``None``, and each of
    those tools has a guard that turns ``None`` into an error. An Agent Host
    agent asking a question got back "requires a durable tool call id" and the
    question reached nobody.
    """
    recorded: list[dict] = []
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()
    agent_run_id = uuid4()
    agent = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="A",
        instruction="ask me",
        toolsets=[AgentToolset.USER_INTERACTION],
    )
    conversation = Conversation(
        id=conversation_id, user_id=user_id, pod_id=pod_id, title="t"
    )
    # supports_pause_signal is False, as it is for every Agent Host run: this is
    # the branch that cannot raise its way out of a turn.
    ctx = ToolContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx, AgentGrantSummary()

    async def fake_record(uow_factory, **kwargs):
        del uow_factory
        recorded.append(kwargs)
        return "lemma-mcp-deadbeef"

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True, grants=None):
        del self, agent, allow_subagents, grants
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.agent_host.mcp.record_pausing_tool_call",
        fake_record,
    )
    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.callables."
        "AgentCallableToolFactory.build_specs",
        fake_callable_toolsets,
    )

    result = await ConversationMCPService().call_tool(
        conversation_id=conversation_id,
        name="lemma_ask_user",
        # Flat, because that is the schema the tool advertises: pydantic-ai
        # unwraps a single request model, so this is the shape a real agent
        # sends -- and the shape the approval card parses back out of what
        # gets recorded.
        arguments={
            "questions": [
                {
                    "question": "Which one?",
                    "header": "Pick",
                    "options": [
                        {"label": "Blue", "description": "the blue one"},
                        {"label": "Red", "description": "the red one"},
                    ],
                }
            ]
        },
        agent_run_id=agent_run_id,
    )

    answer = json.loads(result.content[0].text)
    assert answer["parked_tool_call_id"] == "lemma-mcp-deadbeef", answer
    # Recorded before the tool ran, against the run that made the call, with the
    # arguments the card is rendered from.
    assert recorded[0]["tool_name"] == "ask_user"
    assert recorded[0]["pauses"] is PauseKind.QUESTION
    assert recorded[0]["agent_run_id"] == agent_run_id
    asked = recorded[0]["arguments"]["questions"][0]
    assert asked["question"] == "Which one?"
    assert asked["header"] == "Pick"


@pytest.mark.asyncio
async def test_an_ordinary_tool_is_not_put_on_the_record_first(monkeypatch):
    """Only a call that outlives its own return needs one; the rest would be noise."""
    recorded: list[dict] = []
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()
    agent = Agent(
        pod_id=pod_id,
        user_id=user_id,
        name="A",
        instruction="i",
        toolsets=[AgentToolset.USER_INTERACTION],
    )
    conversation = Conversation(
        id=conversation_id, user_id=user_id, pod_id=pod_id, title="t"
    )
    ctx = ToolContext(user_id=user_id, pod_id=pod_id, conversation_id=conversation_id)

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx, AgentGrantSummary()

    async def fake_record(uow_factory, **kwargs):
        del uow_factory
        recorded.append(kwargs)
        return "lemma-mcp-deadbeef"

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True, grants=None):
        del self, agent, allow_subagents, grants
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.agent_host.mcp.record_pausing_tool_call",
        fake_record,
    )
    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.callables."
        "AgentCallableToolFactory.build_specs",
        fake_callable_toolsets,
    )

    await ConversationMCPService().call_tool(
        conversation_id=conversation_id,
        name="lemma_display_resource",
        arguments={"request": {"type": "TEXT"}},
        agent_run_id=uuid4(),
    )
    assert recorded == []


class _ConversationAlreadyLoaded(ConversationMCPService):
    """The service with its one database read answered in advance.

    Everything after the load -- toolset selection, assembly, MCP naming -- is
    the real path an Agent Host run's `tools/list` takes.
    """

    def __init__(self, conversation: Conversation, ctx: ToolContext) -> None:
        super().__init__()
        self._conversation = conversation
        self._ctx = ctx

    async def _load_agent_context(self, *, conversation_id, agent_run_id):
        del conversation_id, agent_run_id
        return None, self._conversation, self._ctx, AgentGrantSummary()


_SUBAGENT_TOOLS = {
    "lemma_spawn_subagent",
    "lemma_interact_subagent",
    "lemma_query_subagents",
    "lemma_wait_for",
}


async def _agent_host_tool_names(
    *, origin: ConversationOrigin = ConversationOrigin.CHAT
) -> set[str]:
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()
    conversation = Conversation(
        id=conversation_id,
        user_id=user_id,
        pod_id=pod_id,
        title="Delegation",
        origin=origin,
    )
    ctx = ToolContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        is_pod_default_agent=True,
        runtime_profile={"protocol": "AGENT_HOST"},
    )
    service = _ConversationAlreadyLoaded(conversation, ctx)
    return set(
        await service.exported_tool_names(
            conversation_id=conversation_id, agent_run_id=uuid4()
        )
    )


async def test_an_agent_host_run_is_offered_the_subagent_tools_under_their_mcp_names():
    names = await _agent_host_tool_names()

    assert _SUBAGENT_TOOLS <= names


async def test_a_sub_agent_on_agent_host_is_not_offered_the_subagent_tools():
    """The depth rule: a child cannot spawn, or wait inside its parent's turn."""
    names = await _agent_host_tool_names(origin=ConversationOrigin.SUBAGENT)

    assert not _SUBAGENT_TOOLS & names
    # Still a working run, not an empty tool list.
    assert "lemma_ask_user" in names
