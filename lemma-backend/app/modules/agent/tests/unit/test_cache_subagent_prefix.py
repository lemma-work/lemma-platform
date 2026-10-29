"""A sub-agent of the same agent reads its parent's cached prefix.

The cache gate's leg for delegation (``test_cache_stable_prefix`` holds the
rest): a child's first request is cold unless it starts with the bytes its
parent already sent -- the same tools, then the deployment's prompt and the
agent's instructions -- and only then its own framing. A child that is shown no
``delegate`` has a different tool array, and tools render ahead of everything
else, so it shared almost nothing.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import AgentToolset, ConversationOrigin
from app.modules.agent.services.tool_catalog import (
    REFUSED_TO_SUBAGENT,
    ToolSurface,
    specs_for,
)
from app.modules.agent.tests.unit.test_cache_stable_prefix import (
    Model,
    Protocol,
    Sent,
    Storage,
    _run,
    _text,
    _wire,
)

pytestmark = pytest.mark.unit

_FRAMING = "## You are a sub-agent"


def _agent() -> Agent:
    return Agent(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        name="planner",
        instruction="Plan trips.",
        toolsets=[AgentToolset.SUBAGENTS, AgentToolset.MESSAGING, AgentToolset.WAIT],
    )


def _conversation(agent: Agent, **child: object) -> Conversation:
    return Conversation(
        pod_id=agent.pod_id, user_id=agent.user_id, agent_id=agent.id, **child
    )


def _ctx(conversation: Conversation) -> ToolContext:
    return ToolContext(
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
    )


async def _first_request(agent: Agent, conversation: Conversation, text: str) -> Sent:
    tools = await specs_for(
        agent=agent,
        conversation=conversation,
        ctx=_ctx(conversation),
        surface=ToolSurface.IN_PROCESS,
        uow_factory=None,
    )
    storage = Storage(conversation)
    model = Model()
    run_id = uuid4()
    storage.user(text, run_id=run_id)
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run_id,
        script=[_text("Done.")],
        trigger_tokens=10**6,
        tools=tools,
    )
    return model.sent[0]


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_a_child_shares_every_byte_of_its_parents_prefix_up_to_its_framing(
    protocol: Protocol,
) -> None:
    agent = _agent()
    parent_conversation = _conversation(agent)
    child_conversation = _conversation(
        agent,
        parent_id=parent_conversation.id,
        origin=ConversationOrigin.SUBAGENT,
        reports_to_parent=True,
    )

    parent = await _first_request(agent, parent_conversation, "Plan Oslo.")
    child = await _first_request(agent, child_conversation, "Find hotels in Oslo.")

    assert child.tools == parent.tools
    assert parent.instructions is not None and child.instructions is not None
    assert _FRAMING not in parent.instructions
    framing = child.instructions.index(_FRAMING)
    assert child.instructions[:framing] == parent.instructions[:framing]
    # Everything before the framing: the deployment's prompt and the agent's
    # own instruction, which is most of what the parent cached.
    assert "Plan trips." in child.instructions[:framing]
    assert await _wire(child.messages, protocol) != await _wire(
        parent.messages, protocol
    )


@pytest.mark.anyio
async def test_a_child_is_shown_delegate_and_refused_when_it_calls_it() -> None:
    agent = _agent()
    child = _conversation(agent, parent_id=uuid4(), origin=ConversationOrigin.SUBAGENT)
    specs = {
        spec.name: spec
        for spec in await specs_for(
            agent=agent,
            conversation=child,
            ctx=_ctx(child),
            surface=ToolSurface.IN_PROCESS,
            uow_factory=None,
        )
    }

    delegate = specs["delegate"]
    result = await delegate.run(
        _ctx(child), delegate.parse({"tasks": [{"brief": "More hotels."}]})
    )

    assert result == {"success": False, "error": REFUSED_TO_SUBAGENT}
    assert delegate.policy.pauses is None
