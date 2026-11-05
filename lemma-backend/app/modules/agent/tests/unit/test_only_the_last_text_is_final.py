"""Only the text a run ends on is its final answer.

The in-process harness writes a text part the moment it ends, before the rest
of the response has arrived, and the message writer marks unmarked assistant
text final. So the preamble before a tool call -- "Let me check." -- was stored
as a final answer, and a run that answered, went on working and answered again
stored two. A surface delivering "the final answer" then posted the first one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAIAgent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)
from pydantic_ai.tools import RunContext

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.steered_answers import ANSWERED_BEFORE_STEER
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses import pydantic_ai as harness_module
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.pydantic_ai_node_loop import (
    settle_response_answers,
)

pytestmark = pytest.mark.unit


class _Verdict(BaseModel):
    verdict: str


def _scripted(*responses: ModelResponse) -> FunctionModel:
    """Streams each response in turn: its text, then its tool calls."""
    remaining = list(responses)

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del messages, info
        response = remaining.pop(0)
        calls = 0
        for part in response.parts:
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {
                    calls: DeltaToolCall(
                        name=part.tool_name, json_args=part.args_as_json_str()
                    )
                }
                calls += 1

    return FunctionModel(stream_function=stream)


async def _texts(model: FunctionModel, monkeypatch, **options) -> list[MessageDraft]:
    monkeypatch.setattr(harness_module, "_runtime_profile_model", lambda _: model)
    pod_id = uuid4()
    conversation = Conversation(pod_id=pod_id, user_id=uuid4())
    drafts: list[MessageDraft] = []
    async for event in PydanticAIHarness()._execute(
        agent=Agent(
            pod_id=pod_id, user_id=conversation.user_id, name="a", instruction=""
        ),
        conversation=conversation,
        messages=[],
        ctx=AgentContext(
            user_id=conversation.user_id,
            pod_id=pod_id,
            conversation_id=conversation.id,
        ),
        options=HarnessOptions(
            model_name="test-model", history_summarization_enabled=False, **options
        ),
        agent_run_id=uuid4(),
        should_stop=None,
    ):
        if event.type is AgentEventType.MESSAGE and isinstance(
            event.data, MessageDraft
        ):
            draft = event.data
            if draft.role is MessageRole.ASSISTANT and draft.kind is MessageKind.TEXT:
                drafts.append(draft)
    return drafts


def _final(draft: MessageDraft) -> bool:
    # Unmarked assistant text is stored final (`RunMessageWriter.persist`).
    return (draft.metadata or {}).get("is_final_answer", True) is True


@pytest.mark.asyncio
async def test_text_before_a_tool_call_is_not_the_final_answer(monkeypatch) -> None:
    drafts = await _texts(
        _scripted(
            ModelResponse(
                parts=[
                    TextPart("Let me check."),
                    ToolCallPart(tool_name="no_such_tool", args={}),
                ]
            ),
            ModelResponse(parts=[TextPart("It is done.")]),
        ),
        monkeypatch,
    )

    assert [(draft.text, _final(draft)) for draft in drafts] == [
        ("Let me check.", False),
        ("It is done.", True),
    ]


@pytest.mark.asyncio
async def test_an_answer_the_run_goes_on_past_is_not_the_final_answer(
    monkeypatch,
) -> None:
    """A text-only response is final only if the run then ends. Here the run
    is sent back for a structured answer and gives it, so the text before is
    a step on the way."""
    drafts = await _texts(
        _scripted(
            ModelResponse(parts=[TextPart("Looks fine to me.")]),
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="final_result", args={"verdict": "ok"}),
                ]
            ),
        ),
        monkeypatch,
        output_type=_Verdict,
    )

    finals = [draft for draft in drafts if _final(draft)]
    assert [draft.text for draft in drafts if not _final(draft)] == [
        "Looks fine to me."
    ]
    # Sent back for the structured answer, not by the person: no answer of
    # its own to ship.
    assert not any(
        (draft.metadata or {}).get(ANSWERED_BEFORE_STEER) for draft in drafts
    )
    assert len(finals) == 1, drafts
    assert (finals[0].metadata or {}).get("structured_output") == {"verdict": "ok"}


class _SaysMoreAsItAnswers(AbstractCapability[object]):
    """The person writes again as the run gives its answer: their words are
    steered in once, the way ``PendingUserMessagesCapability`` steers them."""

    def __init__(self) -> None:
        self.said = False

    async def before_node_run(self, ctx: RunContext[object], *, node: object) -> object:
        if not self.said and PydanticAIAgent.is_call_tools_node(node):
            self.said = True
            ctx.enqueue("And tomorrow?")
        return node


@pytest.mark.asyncio
async def test_an_answer_the_person_spoke_over_is_an_answer_all_the_same(
    monkeypatch,
) -> None:
    """The run answered; the person said more before it ended, so it went on
    and answered that too. The first answer is not the run's final one, but it
    was given -- marked so it ships as a message of its own."""
    drafts = await _texts(
        _scripted(
            ModelResponse(parts=[TextPart("Sunny today.")]),
            ModelResponse(parts=[TextPart("Rain tomorrow.")]),
        ),
        monkeypatch,
        capabilities=[_SaysMoreAsItAnswers()],
    )

    assert [
        (draft.text, _final(draft), (draft.metadata or {}).get(ANSWERED_BEFORE_STEER))
        for draft in drafts
    ] == [("Sunny today.", False, True), ("Rain tomorrow.", True, None)]


def test_settling_a_response_leaves_only_a_closing_text_undecided() -> None:
    def message(draft: MessageDraft) -> AgentEvent:
        return AgentEvent(type=AgentEventType.MESSAGE, data=draft)

    def shown(events: list[AgentEvent]) -> list[tuple[str | None, object]]:
        return [
            (event.data.text, (event.data.metadata or {}).get("is_final_answer"))
            for event in events
        ]

    call = MessageDraft.of_tool_call(tool_name="t", tool_call_id="c", tool_args={})
    first, second = MessageDraft.of_text("a"), MessageDraft.of_text("b")

    # A tool call means the run goes on: no text of this response is final.
    settled, undecided = settle_response_answers(
        [message(first), message(call), message(second)]
    )
    assert shown(settled) == [("a", False), (None, None), ("b", False)]
    assert undecided == []

    # No tool call: the last text is final if the run ends here, which only
    # the next node can say.
    settled, undecided = settle_response_answers([message(first), message(second)])
    assert shown(settled) == [("a", False)]
    assert shown(undecided) == [("b", None)]
