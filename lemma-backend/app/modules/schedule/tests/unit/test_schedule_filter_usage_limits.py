"""The ceiling the schedule filter asks for, and the bound that backs it up.

``UsageLimits.count_tokens_before_request`` makes pydantic-ai call
``Model.count_tokens`` before the request. The base method raises
``NotImplementedError`` and OpenAI-compatible models do not override it, so
asking for it there fails every LLM-filtered datastore schedule.

Deciding that is not schedule's job and no longer happens here:
``resolve_system_runtime`` takes this module's ceiling and returns the part of
it the resolved model can genuinely honour, and
``test_token_precount_support.py`` in ``agent`` is where that reduction is
proved. What is schedule's, and is asserted below, is the ceiling itself and
the consequence of it being honoured late: when the pre-count is dropped,
``input_tokens_limit`` is only enforced *after* the provider has been called and
billed, so the prompt has to be bounded before it is sent.
"""

from __future__ import annotations

import pytest

from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    _MAX_EVENT_CHARS,
    FILTER_USAGE_LIMITS,
    SystemModelScheduleFilter,
)

pytestmark = pytest.mark.unit

#: Conservative characters-per-token for the models this filter runs on. Under
#: an estimate, deliberately: the budget below has to hold for the worst
#: tokenizer, not the average one.
_CHARS_PER_TOKEN = 3


def test_the_filter_declares_its_own_ceiling() -> None:
    """One request, and caps that add up. The filter runs a single turn, so a
    `request_limit` above one would only mean retries nobody asked for."""
    assert FILTER_USAGE_LIMITS.request_limit == 1
    assert FILTER_USAGE_LIMITS.count_tokens_before_request is True
    assert FILTER_USAGE_LIMITS.total_tokens_limit == (
        (FILTER_USAGE_LIMITS.input_tokens_limit or 0)
        + (FILTER_USAGE_LIMITS.output_tokens_limit or 0)
    )


def test_the_rendered_event_cannot_use_the_whole_input_budget() -> None:
    """`_MAX_EVENT_CHARS` is the half of the budget the caller controls.

    The system prompt and the filter instruction go in the same request, so the
    event has to leave room for them -- and it has to, because a model that
    cannot pre-count refuses an oversized prompt only after it has been billed
    for it."""
    event_tokens = _MAX_EVENT_CHARS / _CHARS_PER_TOKEN
    input_limit = FILTER_USAGE_LIMITS.input_tokens_limit
    assert input_limit is not None
    assert event_tokens < input_limit * 0.7


def test_a_bounded_event_stays_inside_the_bound() -> None:
    """The two constants above only mean something together: the renderer is
    what keeps a payload of any size under `_MAX_EVENT_CHARS`."""
    payload: dict[str, object] = {
        "rows": [{"i": index, "blob": "x" * 200} for index in range(2_000)]
    }

    message = SystemModelScheduleFilter._user_message(payload)

    assert len(message) / _CHARS_PER_TOKEN < (
        FILTER_USAGE_LIMITS.input_tokens_limit or 0
    )


def test_the_filter_never_asks_for_limits_it_did_not_declare() -> None:
    """The ceiling is passed to `agent`, not restated there. If this module
    stops handing `FILTER_USAGE_LIMITS` to `resolve_system_runtime`, the filter
    silently runs on whatever that call's default happens to be."""
    import inspect

    from app.modules.schedule.infrastructure.adapters import system_model_filter

    source = inspect.getsource(system_model_filter)
    assert "resolve_system_runtime(usage_limits=FILTER_USAGE_LIMITS)" in source
    assert "usage_limits=runtime.usage_limits" in source
