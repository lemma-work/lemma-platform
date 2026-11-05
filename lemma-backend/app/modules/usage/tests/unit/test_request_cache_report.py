"""Each model request's usage record says how much of it came from the cache.

A cache hit ratio means nothing without knowing where the request sits: the
first request of a later run should re-read everything the previous run sent,
and a later step re-reads its own run. So the receipt carries the run's place in
its conversation and the step's place in its run, beside the counts.

Where an OpenAI-compatible provider reports cached tokens is settled here
against pydantic-ai's own mapping rather than assumed: it reads the body's
``usage.prompt_tokens_details.cached_tokens`` (through genai-prices, whose
Fireworks extractor maps exactly that path) and never a response header. A
provider that reports cached tokens only in a header reads as a cold cache.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from openai.types.chat import ChatCompletionChunk
from pydantic_ai import RunContext
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import _map_usage
from pydantic_ai.usage import RunUsage

from app.modules.usage.domain.accounting import (
    RUN_INDEX_METADATA_KEY,
    RequestPosition,
    RequestReceipt,
    TokenCounts,
)
from app.modules.usage.infrastructure.metered_model import _position

pytestmark = pytest.mark.unit


def _receipt(position: RequestPosition | None) -> RequestReceipt:
    return RequestReceipt(
        request_id=uuid4(),
        occurred_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
        counts=TokenCounts(
            input_tokens=10_000, cache_read_tokens=9_000, cache_write_tokens=500
        ),
        position=position,
    )


def test_the_report_carries_the_ratio_and_where_the_request_sits() -> None:
    report = _receipt(RequestPosition(run_index=3, step_index=1)).cache_report()

    assert report == {
        "input_tokens": 10_000,
        "cache_read_tokens": 9_000,
        "cache_write_tokens": 500,
        "hit_ratio": 0.9,
        "run_index": 3,
        "step_index": 1,
    }


def test_a_request_with_no_input_has_no_ratio() -> None:
    receipt = RequestReceipt(
        request_id=uuid4(),
        occurred_at=datetime(2026, 10, 28, tzinfo=timezone.utc),
        counts=TokenCounts(request_count=1),
    )

    assert receipt.cache_report()["hit_ratio"] is None
    assert receipt.cache_report()["run_index"] is None


def test_the_position_is_read_from_the_run_the_harness_started() -> None:
    def context(metadata: dict[str, object] | None) -> RunContext[None]:
        return RunContext(
            deps=None,
            model=FunctionModel(lambda messages, info: None),  # type: ignore[arg-type, return-value]
            usage=RunUsage(),
            metadata=metadata,
            run_step=2,
        )

    assert _position(context({RUN_INDEX_METADATA_KEY: 4})) == RequestPosition(
        run_index=4, step_index=2
    )
    # A run nobody numbered still says which step it is on.
    assert _position(context(None)) == RequestPosition(step_index=2)
    assert _position(context({RUN_INDEX_METADATA_KEY: "4"})) == RequestPosition(
        step_index=2
    )
    assert _position(None) is None


def test_fireworks_cached_tokens_are_read_from_the_body() -> None:
    chunk = ChatCompletionChunk.model_validate(
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "accounts/fireworks/models/kimi",
            "choices": [],
            "usage": {
                "prompt_tokens": 12_000,
                "completion_tokens": 40,
                "total_tokens": 12_040,
                "prompt_tokens_details": {"cached_tokens": 11_264},
            },
        }
    )

    usage = _map_usage(
        chunk,
        "fireworks",
        "https://api.fireworks.ai/inference/v1",
        "accounts/fireworks/models/kimi",
    )

    assert usage.input_tokens == 12_000
    assert usage.cache_read_tokens == 11_264
