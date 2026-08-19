from __future__ import annotations

import json

from uuid import uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services.conversation_mcp_service import ConversationMCPService
from app.modules.agent.tools.context import BaseAgentContext


class _SurfaceToolRequest(BaseModel):
    path: str


@pytest.mark.asyncio
async def test_conversation_mcp_includes_surface_toolsets(monkeypatch):
    async def telegram_send_file(
        ctx: RunContext[BaseAgentContext],
        request: _SurfaceToolRequest,
    ) -> dict[str, str]:
        """Send a file to the current Telegram chat."""
        return {
            "conversation_id": str(ctx.deps.conversation_id),
            "path": request.path,
        }

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
        metadata={
            "surface_platform": "TELEGRAM",
            "external_channel_id": "12345",
            "external_thread_id": "12345",
        },
    )
    ctx = BaseAgentContext(
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
        return agent, conversation, ctx

    async def fake_build_toolsets(self, *, conversation):
        del self, conversation
        return [FunctionToolset[BaseAgentContext](tools=[telegram_send_file])]

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True):
        # Unit test: must not touch the real database. The production
        # implementation runs a SQL query whenever the agent has an id (always
        # true, ids are auto-generated), which made this test depend on a live
        # Postgres and on loop-bound engine state shared across the session.
        del self, agent, allow_subagents
        return []

    monkeypatch.setattr(
        ConversationMCPService,
        "_load_agent_context",
        fake_load_agent_context,
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.callable_tool_factory."
        "AgentCallableToolFactory.build_toolsets",
        fake_callable_toolsets,
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory."
        "SurfacePlatformToolFactory.build_toolsets",
        fake_build_toolsets,
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
    ctx = BaseAgentContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_name=agent.name,
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True):
        del self, agent, allow_subagents
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.callable_tool_factory."
        "AgentCallableToolFactory.build_toolsets",
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
    and the id a snooze's wake writes its return under. Nothing on the MCP wire
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
    ctx = BaseAgentContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx

    async def fake_record(uow_factory, **kwargs):
        del uow_factory
        recorded.append(kwargs)
        return "lemma-mcp-deadbeef"

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True):
        del self, agent, allow_subagents
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.services.conversation_mcp_service.record_pausing_tool_call",
        fake_record,
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.callable_tool_factory."
        "AgentCallableToolFactory.build_toolsets",
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
    ctx = BaseAgentContext(
        user_id=user_id, pod_id=pod_id, conversation_id=conversation_id
    )

    async def fake_load_agent_context(self, *, conversation_id, agent_run_id):
        del self, conversation_id, agent_run_id
        return agent, conversation, ctx

    async def fake_record(uow_factory, **kwargs):
        del uow_factory
        recorded.append(kwargs)
        return "lemma-mcp-deadbeef"

    async def fake_callable_toolsets(self, *, agent, allow_subagents=True):
        del self, agent, allow_subagents
        return []

    monkeypatch.setattr(
        ConversationMCPService, "_load_agent_context", fake_load_agent_context
    )
    monkeypatch.setattr(
        "app.modules.agent.services.conversation_mcp_service.record_pausing_tool_call",
        fake_record,
    )
    monkeypatch.setattr(
        "app.modules.agent.tools.callable_tool_factory."
        "AgentCallableToolFactory.build_toolsets",
        fake_callable_toolsets,
    )

    await ConversationMCPService().call_tool(
        conversation_id=conversation_id,
        name="lemma_display_resource",
        arguments={"request": {"type": "TEXT"}},
        agent_run_id=uuid4(),
    )
    assert recorded == []
