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


def test_provider_error_identifiers_extract_the_code_not_the_prose() -> None:
    """A 400 is undiagnosable without knowing *which* 400 it was.

    The logging contract forbids logging bodies, so only the short enum-like
    identifiers are pulled out — never the message, which echoes the request.
    """
    from app.modules.agent.infrastructure.harnesses.provider_error_log import (
        provider_error_identifiers,
    )

    # OpenAI-compatible shape (Fireworks, OpenAI, most gateways).
    kind, code = provider_error_identifiers(
        {
            "error": {
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
                "message": "This model's maximum context length is 128000 tokens",
            }
        }
    )
    assert (kind, code) == ("invalid_request_error", "context_length_exceeded")

    # Anthropic shape puts the envelope type at the top level.
    kind, code = provider_error_identifiers(
        {"type": "error", "error": {"type": "overloaded_error"}}
    )
    assert kind == "overloaded_error"


def test_provider_error_identifiers_refuse_prose_and_secrets() -> None:
    """Anything long enough to be prose is dropped rather than truncated."""
    from app.modules.agent.infrastructure.harnesses.provider_error_log import (
        provider_error_identifiers,
    )

    kind, code = provider_error_identifiers(
        {
            "error": {
                "type": "Incorrect API key provided: sk-secret-should-not-appear",
                "code": "a b c",
            }
        }
    )
    assert kind is None
    assert code is None
    assert provider_error_identifiers("not a dict") == (None, None)
    assert provider_error_identifiers(None) == (None, None)


def test_quota_exhaustion_reads_as_a_limit_not_a_misconfiguration() -> None:
    """154 runs a week hit this; "check the configuration" sent people hunting
    a bug in a system that was working exactly as designed."""
    import httpx

    from app.modules.agent.services.run_finalizer import run_failure_message
    from app.modules.usage.domain.errors import UsageLimitExceededError

    quota = run_failure_message(UsageLimitExceededError("over limit"))
    assert "usage allowance" in quota
    assert "runtime configuration" not in quota

    dropped = run_failure_message(httpx.ReadError("connection reset"))
    assert "Nothing you sent was lost" in dropped

    generic = run_failure_message(ValueError("something else"))
    assert "Agent run failed" in generic
