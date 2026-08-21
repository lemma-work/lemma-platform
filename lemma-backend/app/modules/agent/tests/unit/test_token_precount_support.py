"""Who may be asked to count tokens before the request.

`UsageLimits(count_tokens_before_request=True)` is not a hint. pydantic-ai calls
`Model.count_tokens` and the base implementation raises, so asking a model that
cannot count does not fall back to counting afterwards — it takes the whole call
down. Conversation titles, bundle README polish and schedule filters all asked,
all ran on the OpenAI-compatible system runtime, and all raised.

The subtle half is `WrapperModel`: it overrides `count_tokens` to delegate, so
"does this class override the method" answers yes for every instrumented model
while the model underneath still cannot count.
"""

from __future__ import annotations

import pytest
from pydantic_ai import UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentedModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.services.runtime_model_factory import (
    supports_token_precount,
    usage_limits_for,
)

pytestmark = pytest.mark.unit

_LIMITS = UsageLimits(request_limit=1, count_tokens_before_request=True)


def _openai_compatible() -> OpenAIChatModel:
    """What every system-runtime profile in this deployment resolves to."""
    return OpenAIChatModel(
        "accounts/fireworks/models/some-model",
        provider=OpenAIProvider(api_key="test-key", base_url="https://example.invalid"),
    )


class _Counting(Model):
    """Stands in for a provider that does implement the pre-count."""

    async def count_tokens(self, messages, model_settings, model_request_parameters):
        raise AssertionError("not called: only the presence of the override matters")

    async def request(self, *args, **kwargs):  # pragma: no cover - never invoked
        raise NotImplementedError

    @property
    def model_name(self) -> str:  # pragma: no cover - never invoked
        return "counting"

    @property
    def system(self) -> str:  # pragma: no cover - never invoked
        return "test"


def test_an_openai_compatible_chat_model_cannot_pre_count() -> None:
    assert supports_token_precount(_openai_compatible()) is False


def test_a_model_that_implements_it_can() -> None:
    assert supports_token_precount(_Counting()) is True


def test_instrumentation_does_not_make_a_model_able_to_count() -> None:
    """The case the previous guard got wrong. Instrumentation is always on in
    this deployment, so this is the shape that actually shipped."""
    wrapped = InstrumentedModel(_openai_compatible())

    assert isinstance(wrapped, WrapperModel), "assumption behind the unwrapping"
    assert supports_token_precount(wrapped) is False


def test_instrumentation_does_not_hide_a_model_that_can_count() -> None:
    assert supports_token_precount(InstrumentedModel(_Counting())) is True


def test_limits_drop_the_precount_only_for_models_that_cannot() -> None:
    without = usage_limits_for(_openai_compatible(), _LIMITS)
    with_it = usage_limits_for(_Counting(), _LIMITS)

    assert without.count_tokens_before_request is False
    assert with_it.count_tokens_before_request is True
    # The caps themselves are the caller's, and must survive untouched: the
    # point is to enforce them against reported usage, not to stop enforcing.
    assert without.request_limit == _LIMITS.request_limit


def test_something_that_is_not_a_model_at_all_simply_cannot_count() -> None:
    """A helper whose job is to stop a call raising must not raise itself. The
    model here is whatever the profile resolved to, doubles included."""
    assert supports_token_precount(object()) is False  # type: ignore[arg-type]
    assert usage_limits_for(object(), _LIMITS).count_tokens_before_request is False  # type: ignore[arg-type]


def test_limits_that_never_asked_are_returned_unchanged() -> None:
    limits = UsageLimits(request_limit=3, count_tokens_before_request=False)

    assert usage_limits_for(_openai_compatible(), limits) is limits


@pytest.mark.parametrize(
    "module_path, attribute",
    [
        ("app.composition.schedule_filter", "FILTER_USAGE_LIMITS"),
        (
            "app.modules.agent.services.conversation_title_service",
            "_TITLE_USAGE_LIMITS",
        ),
    ],
)
def test_every_declared_precount_still_goes_through_the_helper(
    module_path: str, attribute: str
) -> None:
    """These constants ask for the pre-count, so each one is only safe because
    its call site routes it through `usage_limits_for`. If a constant stops
    asking, this test has become noise; if a call site stops routing, the
    grep below is what notices."""
    import importlib
    import inspect

    module = importlib.import_module(module_path)
    assert getattr(module, attribute).count_tokens_before_request is True
    assert "usage_limits_for" in inspect.getsource(module)
