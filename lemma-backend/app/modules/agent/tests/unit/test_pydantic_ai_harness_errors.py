from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)

from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.infrastructure.harnesses.pydantic_ai import PydanticAIHarness


@pytest.mark.asyncio
async def test_model_http_error_emits_sanitized_ui_message(monkeypatch) -> None:
    """A 404/500 from the provider must not leak keys or raw bodies to the UI."""
    harness = PydanticAIHarness()
    agent_run_id = UUID("00000000-0000-0000-0000-000000000001")

    async def fake_execute(**_kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise ModelHTTPError(
            status_code=404,
            model_name="deepseek-v4-flash",
            body={
                "message": "Model not found",
                "api_key": "sk-secret-should-not-appear",
            },
        )

    monkeypatch.setattr(harness, "_execute", fake_execute)

    events = [
        event
        async for event in harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-0000000000aa")
            ),
            messages=[],
            ctx=SimpleNamespace(),
            options=SimpleNamespace(should_stop=None),
            agent_run_id=agent_run_id,
        )
    ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.ERROR
    assert isinstance(events[0].data, str)
    assert "HTTP 404" in events[0].data
    assert "deepseek-v4-flash" not in events[0].data
    assert "sk-secret-should-not-appear" not in events[0].data
    assert "Model not found" not in events[0].data
    assert "Please check the agent runtime configuration." in events[0].data


@pytest.mark.asyncio
async def test_unexpected_model_behavior_emits_sanitized_ui_message(
    monkeypatch,
) -> None:
    """Tool retry exhaustion should not forward the raw exception text."""
    harness = PydanticAIHarness()
    agent_run_id = UUID("00000000-0000-0000-0000-000000000002")

    async def fake_execute(**_kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise UnexpectedModelBehavior("Model returned garbage: api_key=super-secret")

    monkeypatch.setattr(harness, "_execute", fake_execute)

    events = [
        event
        async for event in harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-0000000000aa")
            ),
            messages=[],
            ctx=SimpleNamespace(),
            options=SimpleNamespace(should_stop=None),
            agent_run_id=agent_run_id,
        )
    ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.ERROR
    assert "super-secret" not in events[0].data
    assert "Please check the agent configuration." in events[0].data


@pytest.mark.asyncio
async def test_usage_limit_exceeded_emits_sanitized_ui_message(monkeypatch) -> None:
    """Usage limit failures should not leak raw provider details."""
    harness = PydanticAIHarness()
    agent_run_id = UUID("00000000-0000-0000-0000-000000000003")

    async def fake_execute(**_kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise UsageLimitExceeded("token limit exceeded: secret=abc123")

    monkeypatch.setattr(harness, "_execute", fake_execute)

    events = [
        event
        async for event in harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-0000000000aa")
            ),
            messages=[],
            ctx=SimpleNamespace(),
            options=SimpleNamespace(should_stop=None),
            agent_run_id=agent_run_id,
        )
    ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.ERROR
    assert "abc123" not in events[0].data
    assert "Please check the agent runtime configuration." in events[0].data


@pytest.mark.asyncio
async def test_generic_exception_emits_sanitized_ui_message(monkeypatch) -> None:
    """Any other exception must not forward raw error text that may contain keys."""
    harness = PydanticAIHarness()
    agent_run_id = UUID("00000000-0000-0000-0000-000000000004")

    async def fake_execute(**_kwargs):
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise RuntimeError("Authorization: Bearer sk-secret-key")

    monkeypatch.setattr(harness, "_execute", fake_execute)

    events = [
        event
        async for event in harness.run(
            agent=SimpleNamespace(),
            conversation=SimpleNamespace(
                id=UUID("00000000-0000-0000-0000-0000000000aa")
            ),
            messages=[],
            ctx=SimpleNamespace(),
            options=SimpleNamespace(should_stop=None),
            agent_run_id=agent_run_id,
        )
    ]

    assert len(events) == 1
    assert events[0].type == AgentEventType.ERROR
    assert "sk-secret-key" not in events[0].data
    assert "Authorization" not in events[0].data
    assert "Please check the agent runtime configuration." in events[0].data
