"""A filter failure the safety net never saw, and the payload that caused it.

`schedule_consumer` catches `UsageLimitExceededError` — the pod's billing
budget — and records it so the failure breaker can count it, deactivate the
schedule after five, and email the owner. That net was real and never applied
to the failure production actually hits.

pydantic-ai raises its own `UsageLimitExceeded` when a run exceeds
`input_tokens_limit`. Different class, different module, nearly the same name.
It matched nothing, escaped unhandled, and left no ledger row — so nothing
counted, nothing deactivated, nobody was told. It recurred daily for as long as
anyone had been looking.

The cause is that the filter prompt embedded the entire trigger payload, and a
webhook body is whatever the provider chose to send. Real payloads overran the
32,000-token limit several times over.
"""

from __future__ import annotations

import json

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded as PydanticAIUsageLimitExceeded

from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    _MAX_EVENT_CHARS,
    SystemModelScheduleFilter,
)
from app.modules.usage.contracts import UsageLimitExceededError

pytestmark = pytest.mark.unit


def test_a_huge_event_is_truncated_rather_than_failing_the_run():
    """Truncating degrades the filter's judgement on one event. Failing removes
    the filter entirely, silently, on every fire."""
    payload = {"rows": [{"i": index, "blob": "x" * 200} for index in range(2_000)]}

    message = SystemModelScheduleFilter._user_message(payload)

    assert len(message) < _MAX_EVENT_CHARS + 500
    assert "event truncated" in message
    assert "characters omitted" in message


def test_a_small_event_is_passed_through_whole():
    payload = {"ticket": {"id": 7, "status": "open"}}

    message = SystemModelScheduleFilter._user_message(payload)

    assert "event truncated" not in message
    assert json.loads(message.removeprefix("Analyze this event:\n")) == payload


def test_the_rendered_event_is_not_padded_with_indentation():
    """`indent=2` inflated the token count of the one thing already too big."""
    payload = {"a": {"b": {"c": [1, 2, 3]}}}

    message = SystemModelScheduleFilter._user_message(payload)

    assert "\n  " not in message


def test_the_two_usage_exceptions_are_genuinely_unrelated():
    """The trap, pinned. If these ever share a base class, the narrower catch
    in schedule_consumer becomes safe again — until then it must name both."""
    assert not issubclass(PydanticAIUsageLimitExceeded, UsageLimitExceededError)
    assert not issubclass(UsageLimitExceededError, PydanticAIUsageLimitExceeded)
    assert PydanticAIUsageLimitExceeded.__name__ != UsageLimitExceededError.__name__
