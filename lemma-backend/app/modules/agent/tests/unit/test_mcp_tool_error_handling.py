"""Tool errors on the remote-harness path must come back as recoverable results.

The per-conversation / pod MCP services and the approval executor all dispatch
through ``AgentToolDispatcher``. When a tool raises (unknown tool, invalid args,
or an execution error) the harness must receive an MCP tool error (``isError``) /
a structured error payload — never an exception that aborts the run. Control-flow
exceptions still propagate.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai import ModelRetry

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.services.conversation_mcp_service import ConversationMCPService
from app.modules.agent.services.pod_mcp_service import PodMCPService
from app.modules.agent.tools.approval.executor import ApprovalExecutor
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.dispatcher import AgentToolDispatcher, UnknownToolError


def _ctx(conversation_id, pod_id, user_id) -> BaseAgentContext:
    return BaseAgentContext(
        user_id=user_id,
        pod_id=pod_id,
        conversation_id=conversation_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [RuntimeError("tool blew up"), UnknownToolError("nope"), ValueError("bad args")],
)
async def test_conversation_mcp_returns_is_error_on_tool_failure(monkeypatch, exc):
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()
    agent = Agent(pod_id=pod_id, user_id=user_id, name="A", instruction="")
    conversation = Conversation(id=conversation_id, pod_id=pod_id, user_id=user_id)
    ctx = _ctx(conversation_id, pod_id, user_id)

    async def fake_load(self, *, conversation_id, agent_run_id):
        return agent, conversation, ctx

    async def raising_call_tool(self, **kwargs):
        raise exc

    monkeypatch.setattr(ConversationMCPService, "_load_agent_context", fake_load)
    monkeypatch.setattr(AgentToolDispatcher, "call_tool", raising_call_tool)

    service = ConversationMCPService()
    result = await service.call_tool(
        conversation_id=conversation_id, name="lemma_do_thing", arguments={}
    )

    assert result.isError is True
    assert str(exc) in result.content[0].text
    assert result.structuredContent["success"] is False
    # The lemma_ prefix is normalized off before dispatch.
    assert result.structuredContent["tool"] == "do_thing"


@pytest.mark.asyncio
async def test_conversation_mcp_reraises_control_flow(monkeypatch):
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()
    agent = Agent(pod_id=pod_id, user_id=user_id, name="A", instruction="")
    conversation = Conversation(id=conversation_id, pod_id=pod_id, user_id=user_id)
    ctx = _ctx(conversation_id, pod_id, user_id)

    async def fake_load(self, *, conversation_id, agent_run_id):
        return agent, conversation, ctx

    async def raising_call_tool(self, **kwargs):
        raise ModelRetry("retry please")

    monkeypatch.setattr(ConversationMCPService, "_load_agent_context", fake_load)
    monkeypatch.setattr(AgentToolDispatcher, "call_tool", raising_call_tool)

    service = ConversationMCPService()
    with pytest.raises(ModelRetry):
        await service.call_tool(
            conversation_id=conversation_id, name="lemma_do_thing", arguments={}
        )


@pytest.mark.asyncio
async def test_pod_mcp_returns_is_error_on_tool_failure(monkeypatch):
    user_id, pod_id = uuid4(), uuid4()
    ctx = _ctx(uuid4(), pod_id, user_id)

    async def fake_require_context(self, *, pod_id, token):
        return ctx

    async def raising_call_tool(self, **kwargs):
        raise RuntimeError("pod tool blew up")

    monkeypatch.setattr(PodMCPService, "_require_context", fake_require_context)
    monkeypatch.setattr(AgentToolDispatcher, "call_tool", raising_call_tool)

    service = PodMCPService()
    result = await service.call_tool(
        pod_id=pod_id, token="t", name="lemma_query_run", arguments={}
    )

    assert result.isError is True
    assert "pod tool blew up" in result.content[0].text
    assert result.structuredContent["tool"] == "query_run"


@pytest.mark.asyncio
async def test_approval_executor_returns_error_on_tool_failure(monkeypatch):
    user_id, pod_id, conversation_id = uuid4(), uuid4(), uuid4()

    class _FakeUoW:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeConvRepo:
        def __init__(self, _uow):
            pass

        async def get_conversation(self, _cid, include_runs=False):
            return SimpleNamespace(agent_id=None)

    monkeypatch.setattr(
        "app.modules.agent.tools.approval.executor.ConversationRepository",
        _FakeConvRepo,
    )

    async def raising_call_tool(self, **kwargs):
        raise RuntimeError("approved tool failed")

    monkeypatch.setattr(AgentToolDispatcher, "call_tool", raising_call_tool)

    executor = ApprovalExecutor(lambda: _FakeUoW())
    deps = _ctx(conversation_id, pod_id, user_id)
    result = await executor.execute_as_user(
        deps=deps, tool_name="exec_command", args={"cmd": "ls"}
    )

    assert result == {
        "success": False,
        "error": "approved tool failed",
        "error_type": "RuntimeError",
        "tool": "exec_command",
    }
