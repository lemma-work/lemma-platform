"""The run must tell pydantic-ai which conversation it belongs to.

Phoenix groups traces into a session by `session.id` and by nothing else, and on
the model spans that value does not come from us: the OpenInference
instrumentation derives it from pydantic-ai's `gen_ai.conversation.id`. Left
unset, pydantic-ai takes that id from the most recent conversation id on
`message_history` -- and we rebuild history from the database on every run, so
there is never one to inherit and it mints a fresh UUID7 per run.

The result was one session per turn. On the deployment where this was found:
648 traces, 648 sessions, one-to-one, with no way to see a conversation whole.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import HarnessOptions
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness

pytestmark = pytest.mark.unit


async def _answer(messages, info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "done"


async def _run_capturing_iter_kwargs(conversation: Conversation):
    """Run the harness, returning the kwargs it handed to `Agent.iter`.

    Its own patch context, not the test's: a caller runs this more than once, and
    patches that outlive a call stack onto each other -- the second run's wrapper
    closes over the first run's wrapper, so both calls end up writing into both
    dicts and two different conversations read as one.
    """
    captured: dict[str, object] = {}
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            harness_module,
            "_runtime_profile_model",
            lambda options: FunctionModel(stream_function=_answer),
        )
        original_iter = harness_module.PydanticAIAgent.iter

        def _capturing_iter(self, *args, **kwargs):
            captured.update(kwargs)
            return original_iter(self, *args, **kwargs)

        monkeypatch.setattr(harness_module.PydanticAIAgent, "iter", _capturing_iter)
        await _drain(conversation)
    return captured


async def _drain(conversation: Conversation) -> None:
    async for _ in PydanticAIHarness()._execute(
        agent=Agent(
            pod_id=conversation.pod_id,
            user_id=conversation.user_id,
            name="assistant",
            instruction="",
        ),
        conversation=conversation,
        messages=[],
        ctx=AgentContext(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            conversation_id=conversation.id,
        ),
        options=HarnessOptions(
            model_name="test-model",
            history_summarization_enabled=False,
        ),
        agent_run_id=uuid4(),
        malformed_tool_call_ids=set(),
        emitted_tool_response_ids=set(),
        should_stop=None,
    ):
        pass


@pytest.mark.asyncio
async def test_the_run_is_tagged_with_our_conversation_id() -> None:
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    captured = await _run_capturing_iter_kwargs(conversation)

    assert captured["conversation_id"] == str(conversation.id)


@pytest.mark.asyncio
async def test_two_runs_of_one_conversation_share_the_id() -> None:
    """The point of the whole exercise: turns of one conversation group together."""
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4())

    first = await _run_capturing_iter_kwargs(conversation)
    second = await _run_capturing_iter_kwargs(conversation)

    assert first["conversation_id"] == second["conversation_id"] == str(conversation.id)


@pytest.mark.asyncio
async def test_separate_conversations_do_not_collide() -> None:
    one = await _run_capturing_iter_kwargs(
        Conversation(pod_id=uuid4(), user_id=uuid4())
    )
    two = await _run_capturing_iter_kwargs(
        Conversation(pod_id=uuid4(), user_id=uuid4())
    )

    assert one["conversation_id"] != two["conversation_id"]
