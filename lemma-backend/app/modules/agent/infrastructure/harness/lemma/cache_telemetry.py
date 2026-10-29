"""How much of each model request the provider read back from its prompt cache.

pydantic-ai's own instrumentation already judges every response against the
conversation's established prefix (``pydantic_ai.cache.*`` attributes and a
``pydantic_ai.cache.collapse`` event on the request span, keyed by the run's
``conversation_id``). What it cannot say is where the request sits in Lemma's
terms: the first request of a conversation's third run is meant to re-read
everything the second run sent, while a run's fifth step re-reads its own run.
So each response adds one event carrying the counts and that position, beside
the usage record ``MeteredModel`` writes with the same numbers.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

#: The span event each model response adds.
CACHE_EVENT = "lemma.model_request.cache"


def cache_attributes(
    response: ModelResponse, *, run_index: int | None, step_index: int
) -> dict[str, int | float]:
    """The response's cache use, as span attributes (OTel takes no None)."""
    usage = response.usage
    attributes: dict[str, int | float] = {
        "lemma.cache.input_tokens": usage.input_tokens,
        "lemma.cache.read_tokens": usage.cache_read_tokens,
        "lemma.cache.write_tokens": usage.cache_write_tokens,
        "lemma.run.step_index": step_index,
    }
    if usage.input_tokens:
        attributes["lemma.cache.hit_ratio"] = round(
            usage.cache_read_tokens / usage.input_tokens, 4
        )
    if run_index is not None:
        attributes["lemma.run.index"] = run_index
    return attributes


class CacheTelemetry(AbstractCapability[Any]):
    """Records each response's prompt-cache use on the current span."""

    def __init__(self, *, run_index: int | None) -> None:
        self._run_index = run_index

    @classmethod
    def get_serialization_name(cls) -> str | None:  # pragma: no cover - metadata
        return "lemma_cache_telemetry"

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(
                CACHE_EVENT,
                attributes=cache_attributes(
                    response, run_index=self._run_index, step_index=ctx.run_step
                ),
            )
        return response


__all__ = ["CACHE_EVENT", "CacheTelemetry", "cache_attributes"]
