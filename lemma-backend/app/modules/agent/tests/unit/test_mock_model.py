from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.modules.agent.infrastructure.harnesses.mock_model import (
    MOCK_SCRIPT_METADATA_KEY,
    build_mock_model,
    is_mock_llm_enabled,
)


def _conversation(metadata: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=metadata)


def test_is_mock_llm_enabled_follows_setting(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "e2e_llm_mode", "mock")
    assert is_mock_llm_enabled() is True
    monkeypatch.setattr(config.settings, "e2e_llm_mode", "real")
    assert is_mock_llm_enabled() is False


@pytest.mark.asyncio
async def test_default_response_echoes_user_message():
    agent = Agent(build_mock_model(_conversation()))
    result = await agent.run("What is the capital of Japan?")
    assert result.output == "[mock] What is the capital of Japan?"


@pytest.mark.asyncio
async def test_scripted_text_turn_returns_exact_answer():
    conv = _conversation({MOCK_SCRIPT_METADATA_KEY: [{"text": "Tokyo."}]})
    agent = Agent(build_mock_model(conv))
    result = await agent.run("capital of Japan?")
    assert result.output == "Tokyo."


@pytest.mark.asyncio
async def test_scripted_tool_call_then_final_drives_real_tool_loop():
    """A scripted tool call is REALLY executed by the agent loop, then the next
    scripted turn produces the final answer using the tool result."""
    conv = _conversation(
        {
            MOCK_SCRIPT_METADATA_KEY: [
                {"tool_calls": [{"tool_name": "lookup", "args": {"q": "japan"}}]},
                {"text": "The capital is Tokyo."},
            ]
        }
    )
    agent = Agent(build_mock_model(conv))
    calls: list[str] = []

    @agent.tool_plain
    def lookup(q: str) -> str:
        calls.append(q)
        return "Tokyo"

    result = await agent.run("capital of Japan?")

    assert calls == ["japan"]  # the tool actually ran
    assert result.output == "The capital is Tokyo."


@pytest.mark.asyncio
async def test_script_exhaustion_closes_run():
    # Script has only a tool turn; after the tool runs the model is asked again
    # with no turn left → it must close out rather than loop forever.
    conv = _conversation(
        {
            MOCK_SCRIPT_METADATA_KEY: [
                {"tool_calls": [{"tool_name": "noop", "args": {}}]}
            ]
        }
    )
    agent = Agent(build_mock_model(conv))

    @agent.tool_plain
    def noop() -> str:
        return "ok"

    result = await agent.run("go")
    assert result.output == "[mock] done"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_error"),
    [
        ("model_http", ModelHTTPError),
        ("unexpected_model_behavior", UnexpectedModelBehavior),
        ("usage_limit", UsageLimitExceeded),
        ("generic", RuntimeError),
    ],
)
async def test_scripted_provider_errors_reach_the_real_harness_handler(
    kind: str,
    expected_error: type[Exception],
):
    conv = _conversation(
        {
            MOCK_SCRIPT_METADATA_KEY: [
                {
                    "error": {
                        "kind": kind,
                        "message": "CANARY_MODEL_ERROR",
                        "status_code": 429,
                    }
                }
            ]
        }
    )
    agent = Agent(build_mock_model(conv))

    with pytest.raises(expected_error) as captured:
        await agent.run("trigger the provider failure")

    assert "CANARY_MODEL_ERROR" in str(captured.value)
    if kind == "model_http":
        assert isinstance(captured.value, ModelHTTPError)
        assert captured.value.status_code == 429


@pytest.mark.asyncio
async def test_a_structured_output_agent_completes_without_a_script():
    """The mock has to satisfy the schema it is shown, not send `{}` and hope.

    It used to call the output tool with an empty object and call that
    best-effort. It is not: a schema with a required field rejects `{}`,
    pydantic-ai asks for a retry, and the mock -- having no script -- answers
    with the same empty object every time until the run dies on the retry
    ceiling. Two `provider`-marked workflow e2e tests failed exactly this way,
    reported as "a tool failed repeatedly ... check the agent configuration",
    which points at the agent and not at the mock.
    """
    from pydantic import BaseModel

    class Output(BaseModel):
        items: list[str]
        title: str
        count: int
        ready: bool

    agent = Agent(build_mock_model(_conversation()), output_type=Output)

    result = await agent.run("summarise this")

    assert result.output == Output(items=[], title="", count=0, ready=False)


@pytest.mark.asyncio
async def test_a_scripted_structured_output_still_wins():
    """The zero-valued default is a fallback, not an override."""
    from pydantic import BaseModel

    class Output(BaseModel):
        answer: str

    conv = _conversation(
        {
            MOCK_SCRIPT_METADATA_KEY: [
                {
                    "tool_calls": [
                        {"tool_name": "final_result", "args": {"answer": "Tokyo"}}
                    ]
                }
            ]
        }
    )
    agent = Agent(build_mock_model(conv), output_type=Output)

    result = await agent.run("capital of Japan?")

    assert result.output == Output(answer="Tokyo")


async def test_a_scripted_turn_reports_the_token_counts_it_declared():
    """The seam every cost assertion downstream depends on.

    pydantic-ai's estimator reports a flat ~50 input tokens per request and never
    a cached one, so without this no test could assert what a run cost or
    exercise the cached-input discount end to end.
    """
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from app.modules.test_support.e2e.scripted_model import script_text, with_usage

    conversation = SimpleNamespace(
        metadata={
            "mock_llm_script": [
                with_usage(
                    script_text("hello"),
                    input_tokens=1000,
                    output_tokens=250,
                    cache_read_tokens=800,
                    cache_write_tokens=50,
                )
            ]
        }
    )
    model = build_mock_model(conversation)
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    response = await model.request(messages, None, ModelRequestParameters())

    assert response.usage.input_tokens == 1000
    assert response.usage.output_tokens == 250
    assert response.usage.cache_read_tokens == 800
    assert response.usage.cache_write_tokens == 50


async def test_an_unscripted_turn_keeps_the_estimated_usage():
    """Scripting usage is opt-in; every existing test keeps its old behaviour."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    model = build_mock_model(None)
    messages = [ModelRequest(parts=[UserPromptPart(content="hi")])]

    response = await model.request(messages, None, ModelRequestParameters())

    assert response.usage.cache_read_tokens == 0
