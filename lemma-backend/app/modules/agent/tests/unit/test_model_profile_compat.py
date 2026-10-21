"""What a model's own pydantic-ai profile changes about the request we send.

Two things a profile decides that used to fail runs, both read from the
profile's flags rather than from a list of model names here:

* **A thinking budget a model refuses.** pydantic-ai raises before dispatch for
  a budget sent to a model whose profile disallows budget thinking. The budget
  is restated as the nearest unified thinking level, which reaches such a model
  as adaptive thinking.
* **How structured output is asked for.** On a model that thinks by default,
  pydantic-ai's default for a structured output became native JSON-schema
  output, which some Anthropic-compatible gateways refuse. Ours name a tool.

The model names below are test data: they pick the pydantic-ai profile, whose
flags are what the code reads.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import OutputSpec
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.anthropic import AnthropicModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.settings import ModelSettings

from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.value_objects import ConversationType, JsonObject
from app.modules.agent.infrastructure.harnesses import mock_model
from app.modules.agent.services import runtime_model_factory as factory
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.anthropic_thinking import (
    AdaptiveThinkingAnthropicModel,
    adaptive_in_place_of_budget,
    nearest_thinking_effort,
)
from app.modules.agent.tools.final_answer import get_final_answer_tool

pytestmark = pytest.mark.unit

_REFUSES_BUDGETS = AnthropicModelProfile(anthropic_disallows_budget_thinking=True)
_TAKES_BUDGETS = AnthropicModelProfile(anthropic_disallows_budget_thinking=False)
_BUDGET: ModelSettings = {
    "max_tokens": 8000,
    "anthropic_thinking": {"type": "enabled", "budget_tokens": 2000},
}  # type: ignore[typeddict-unknown-key]


class _Gateway:
    """An Anthropic-compatible endpoint that records what it was sent."""

    def __init__(self) -> None:
        self.bodies: list[JsonObject] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )


@pytest.fixture
def gateway() -> _Gateway:
    return _Gateway()


def _model(model_name: str, gateway: _Gateway) -> AdaptiveThinkingAnthropicModel:
    return AdaptiveThinkingAnthropicModel(
        model_name,
        provider=AnthropicProvider(
            anthropic_client=AsyncAnthropic(
                api_key="test-key",
                max_retries=0,
                http_client=httpx2.AsyncClient(
                    transport=httpx2.MockTransport(gateway.handle)
                ),
            )
        ),
    )


async def _send(model_name: str, settings: ModelSettings, gateway: _Gateway) -> None:
    await _model(model_name, gateway).request(
        [ModelRequest.user_text_prompt("hi")], settings, ModelRequestParameters()
    )


def test_an_anthropic_compatible_profile_builds_the_translating_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mock_model.settings, "e2e_llm_mode", "real")
    model = factory.connection_check_model(
        runtime_profile={
            "protocol": "ANTHROPIC_COMPATIBLE",
            "provider_model_name": "claude-haiku-5-5",
            "profile_id": "p",
            "config": {"base_url": "https://gateway.example.test"},
        },
        runtime_credentials={"api_key": "test-key"},
    )
    assert isinstance(model, AdaptiveThinkingAnthropicModel)


async def test_a_budget_reaches_a_model_that_refuses_one_as_adaptive_thinking(
    gateway: _Gateway,
) -> None:
    """It used to raise ``UserError`` here, before anything was sent."""
    await _send("claude-haiku-5-5", _BUDGET, gateway)

    (body,) = gateway.bodies
    assert body["thinking"]["type"] == "adaptive"
    assert "budget_tokens" not in body["thinking"]


async def test_a_model_that_takes_budgets_still_gets_the_budget(
    gateway: _Gateway,
) -> None:
    await _send("claude-sonnet-4-5", _BUDGET, gateway)

    (body,) = gateway.bodies
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2000}


def test_the_budget_becomes_the_nearest_level() -> None:
    assert nearest_thinking_effort(1) == "minimal"
    assert nearest_thinking_effort(2000) == "low"
    assert nearest_thinking_effort(9000) == "medium"
    assert nearest_thinking_effort(16000) == "high"
    assert nearest_thinking_effort(100_000) == "xhigh"


def test_only_a_refused_budget_is_restated() -> None:
    assert adaptive_in_place_of_budget(_TAKES_BUDGETS, _BUDGET) is _BUDGET
    unbudgeted: ModelSettings = {"max_tokens": 10}
    assert adaptive_in_place_of_budget(_REFUSES_BUDGETS, unbudgeted) is unbudgeted
    assert adaptive_in_place_of_budget(_REFUSES_BUDGETS, None) is None
    assert adaptive_in_place_of_budget(_REFUSES_BUDGETS, _BUDGET) == {
        "max_tokens": 8000,
        "thinking": "low",
    }


def test_a_level_the_request_already_names_is_kept() -> None:
    named: ModelSettings = {**_BUDGET, "thinking": "high"}
    assert adaptive_in_place_of_budget(_REFUSES_BUDGETS, named) == {
        "max_tokens": 8000,
        "thinking": "high",
    }


# A model that thinks unless told not to, can produce native JSON-schema output,
# and stops thinking when a tool call is forced: the profile on which
# pydantic-ai's default for a structured output is native rather than a tool.
_THINKS_BY_DEFAULT = ModelProfile(
    supports_thinking=True,
    thinking_enabled_by_default=True,
    supports_json_schema_output=True,
    forced_tool_choice_disables_thinking=True,
)


async def _output_mode(output_type: OutputSpec[object]) -> str:
    modes: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        modes.append(info.model_request_parameters.output_mode)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result",
                    {"status": "COMPLETED", "output": "done"},
                )
            ]
        )

    agent = PydanticAgent(
        FunctionModel(respond, profile=_THINKS_BY_DEFAULT), output_type=output_type
    )
    async with agent.iter("go", deps=object()) as run:
        async for _ in run:
            if modes:
                break
    return modes[0]


def _task_output_type() -> OutputSpec[object]:
    agent = Agent(pod_id=uuid4(), user_id=uuid4(), name="a", instruction="")
    runner = AgentRunnerService(uow_factory=object(), harness_registry=object())
    conversation = Conversation(
        pod_id=agent.pod_id,
        user_id=agent.user_id,
        agent_id=agent.id,
        type=ConversationType.TASK,
    )
    output_type = runner._resolve_output_type(agent, conversation)
    assert output_type is not None
    return output_type


async def test_a_task_asks_for_its_final_answer_through_a_tool() -> None:
    assert await _output_mode(_task_output_type()) == "tool"


async def test_left_to_the_default_the_same_answer_would_be_native() -> None:
    """The control: the profile above does switch an unnamed output mode."""
    agent = Agent(pod_id=uuid4(), user_id=uuid4(), name="a", instruction="")
    assert await _output_mode(get_final_answer_tool(agent)) == "native"
