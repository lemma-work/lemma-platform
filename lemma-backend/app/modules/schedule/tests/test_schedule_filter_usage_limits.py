"""The schedule filter must only ask for a pre-count the model can do.

``UsageLimits.count_tokens_before_request`` makes pydantic-ai call
``Model.count_tokens`` before the request. The base method raises
``NotImplementedError`` and OpenAI-compatible models do not override it, so
asking for it there fails every LLM-filtered datastore schedule.
"""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from app.composition.schedule_filter import (
    FILTER_USAGE_LIMITS,
    FILTER_USAGE_LIMITS_WITHOUT_PRECOUNT,
    filter_usage_limits_for,
)


class _CountingModel(Model):
    """Stand-in for a provider that implements token counting."""

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        raise AssertionError("not called in this test")

    async def request(self, messages, model_settings, model_request_parameters):
        raise AssertionError("not called in this test")

    @property
    def model_name(self) -> str:
        return "counting"

    @property
    def system(self) -> str:
        return "counting"


class _NonCountingModel(Model):
    """Stand-in for a provider that inherits the raising base method."""

    async def request(self, messages, model_settings, model_request_parameters):
        raise AssertionError("not called in this test")

    @property
    def model_name(self) -> str:
        return "non-counting"

    @property
    def system(self) -> str:
        return "non-counting"


def test_model_without_token_counting_skips_the_precount() -> None:
    limits = filter_usage_limits_for(_NonCountingModel())
    assert limits is FILTER_USAGE_LIMITS_WITHOUT_PRECOUNT
    assert limits.count_tokens_before_request is False


def test_model_with_token_counting_keeps_the_precount() -> None:
    limits = filter_usage_limits_for(_CountingModel())
    assert limits is FILTER_USAGE_LIMITS
    assert limits.count_tokens_before_request is True


def test_both_limit_sets_enforce_the_same_caps() -> None:
    """Dropping the pre-count must not quietly widen the budget."""
    for field in (
        "request_limit",
        "input_tokens_limit",
        "output_tokens_limit",
        "total_tokens_limit",
    ):
        assert getattr(FILTER_USAGE_LIMITS, field) == getattr(
            FILTER_USAGE_LIMITS_WITHOUT_PRECOUNT, field
        ), field


def test_real_provider_classes_are_classified_correctly() -> None:
    """Guards the assumption against a pydantic-ai upgrade changing it."""
    assert OpenAIChatModel.count_tokens is Model.count_tokens
    assert AnthropicModel.count_tokens is not Model.count_tokens
