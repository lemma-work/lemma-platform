from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent

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
        {MOCK_SCRIPT_METADATA_KEY: [{"tool_calls": [{"tool_name": "noop", "args": {}}]}]}
    )
    agent = Agent(build_mock_model(conv))

    @agent.tool_plain
    def noop() -> str:
        return "ok"

    result = await agent.run("go")
    assert result.output == "[mock] done"
