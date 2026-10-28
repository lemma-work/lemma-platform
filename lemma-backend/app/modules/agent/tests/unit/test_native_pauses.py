"""Pauses as pydantic-ai deferred calls: every pause of a step, and its siblings.

A pausing tool writes its pause, then raises; the toolset turns that into
``CallDeferred``. The step's other calls run to the end -- they used to be
cancelled by the first pause's exception and closed as never returned -- and
the run ends on ``DeferredToolRequests``, waiting on every pause the step
opened. The next run takes in the returns the pauses were resolved with, from
the log, as it does any answer.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.tools import (
    AgentInputRequired,
    ToolGroup,
    ToolPolicy,
    tool,
)
from app.modules.agent.domain.value_objects import (
    AgentEventType,
    ConversationType,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.tests.unit.test_cache_stable_prefix import (
    Model,
    Storage,
    _run,
    _text,
)

pytestmark = pytest.mark.unit


class Ask(BaseModel):
    question: str


async def ask_user(ctx: ToolContext, request: Ask) -> str:
    """Ask the person something and wait for the answer."""
    assert ctx.tool_call_id is not None
    raise AgentInputRequired(ctx.tool_call_id, PauseKind.QUESTION)


class Approve(BaseModel):
    action: str


async def request_approval(ctx: ToolContext, request: Approve) -> str:
    """Ask the person to approve an action."""
    assert ctx.tool_call_id is not None
    raise AgentInputRequired(ctx.tool_call_id, PauseKind.APPROVAL)


class Work(BaseModel):
    seconds: float


async def slow_work(ctx: ToolContext, request: Work) -> dict[str, object]:
    """Some work that takes a while."""
    await asyncio.sleep(request.seconds)
    return {"done": True}


_TOOLS = [
    tool(ask_user, ToolGroup.CALLABLES, ToolPolicy(pauses=PauseKind.QUESTION)),
    tool(
        request_approval,
        ToolGroup.CALLABLES,
        ToolPolicy(pauses=PauseKind.APPROVAL),
    ),
    tool(slow_work, ToolGroup.CALLABLES),
]


def _calls(*calls: tuple[str, str, str]) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(name, args, tool_call_id=call_id)
            for name, args, call_id in calls
        ]
    )


def _setup(kind: ConversationType = ConversationType.CHAT) -> tuple[Agent, Storage]:
    conversation = Conversation(
        pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4(), type=kind
    )
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="helper",
        instruction="Help.",
    )
    return agent, Storage(conversation)


def _drafts(events: list, kind: MessageKind) -> list[MessageDraft]:
    return [
        event.data
        for event in events
        if isinstance(event.data, MessageDraft) and event.data.kind is kind
    ]


@pytest.mark.anyio
async def test_two_pauses_and_a_slow_sibling_in_one_step() -> None:
    """Both pauses are opened, the sibling finishes, and the run waits on both;
    once both are resolved, the next run reads both answers and the sibling's
    result, as they were."""
    agent, storage = _setup()
    model = Model()
    run1 = uuid4()
    storage.user("Clean up and tell me.", run_id=run1)
    events = await _run(
        model,
        storage,
        agent=agent,
        run_id=run1,
        script=[
            _calls(
                ("ask_user", '{"question": "Which folder?"}', "ask-1"),
                ("request_approval", '{"action": "delete"}', "approve-1"),
                ("slow_work", '{"seconds": 0.05}', "work-1"),
            )
        ],
        trigger_tokens=10**6,
        tools=_TOOLS,
    )

    assert events[-1].type is AgentEventType.WAITING
    assert set(events[-1].data["tool_call_ids"]) == {"ask-1", "approve-1"}
    returns = _drafts(events, MessageKind.TOOL_RETURN)
    assert [(draft.tool_call_id, draft.tool_result) for draft in returns] == [
        ("work-1", {"done": True})
    ], "the sibling ran to the end; no pause was closed as never returned"
    assert not [e for e in events if e.type is AgentEventType.ERROR]

    for call_id, name, answer in (
        ("ask-1", "ask_user", {"answers": {"reply": "/tmp"}}),
        ("approve-1", "request_approval", {"approved": True}),
    ):
        storage.write(
            MessageDraft.of_tool_return(
                tool_call_id=call_id, tool_name=name, tool_result=answer
            ),
            run_id=run1,
        )
    run2 = uuid4()
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run2,
        script=[_text("Deleted /tmp.")],
        trigger_tokens=10**6,
        tools=_TOOLS,
    )

    resumed = model.sent[-1].messages[-1]
    answered = {
        part.tool_call_id: part.content
        for part in resumed.parts
        if hasattr(part, "tool_call_id") and hasattr(part, "content")
    }
    assert answered["ask-1"] == {"answers": {"reply": "/tmp"}}
    assert answered["approve-1"] == {"approved": True}
    assert storage.checkpoints == []


@pytest.mark.anyio
async def test_a_final_answer_beside_a_pause_steps_aside() -> None:
    """pydantic-ai lets a final result win over a deferred call in the same
    response: the task would end "done" and the question would never reach
    the person. The final answer steps aside; the run waits on the question."""
    agent, storage = _setup(ConversationType.TASK)
    model = Model()
    run1 = uuid4()
    storage.user("Do the task.", run_id=run1)
    events = await _run(
        model,
        storage,
        agent=agent,
        run_id=run1,
        script=[
            _calls(
                ("ask_user", '{"question": "Which one?"}', "ask-1"),
                (
                    "final_answer",
                    '{"status": "COMPLETED", "output": "done"}',
                    "final-1",
                ),
            )
        ],
        trigger_tokens=10**6,
        tools=_TOOLS,
    )

    assert events[-1].type is AgentEventType.WAITING
    assert events[-1].data["tool_call_ids"] == ["ask-1"]
    texts = [
        draft.text
        for draft in _drafts(events, MessageKind.TEXT)
        if draft.text and "done" in draft.text
    ]
    assert texts == [], "no final answer was written"


@pytest.mark.anyio
async def test_a_text_answer_beside_nothing_still_completes() -> None:
    agent, storage = _setup()
    model = Model()
    run1 = uuid4()
    storage.user("Hi.", run_id=run1)
    events = await _run(
        model,
        storage,
        agent=agent,
        run_id=run1,
        script=[ModelResponse(parts=[TextPart("Hello.")])],
        trigger_tokens=10**6,
        tools=_TOOLS,
    )

    assert events[-1].type is AgentEventType.COMPLETED
