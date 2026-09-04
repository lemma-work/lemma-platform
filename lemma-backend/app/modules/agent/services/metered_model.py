"""A model wrapper that bills for requests nothing else is counting.

History compaction is the case this exists for. The summarizer builds its own
bare `Agent` internally and calls it without threading the run's usage through,
so those tokens never reach `run.usage` and never reached a usage record --
`summarization_model`'s own docstring says as much: "one that Lemma never
metered". Each compaction is a ~70k-token request on the deployment's own
credentials, repeated every time a long conversation crosses the threshold.

Wrapping the *model* rather than the processor is what makes this safe: the
third-party summarizer is untouched, and anything else handed the same wrapped
model is metered for free. Only `request` is overridden, because that is the one
`Agent.run` uses; a streaming summarizer would need `request_stream` too, and
there is no such thing today.

The recording never fails the run. A compaction that succeeded and then could not
write its usage row must still return the compacted history -- refusing it would
turn a metering problem into a broken conversation.
"""

from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from sqlalchemy.exc import SQLAlchemyError

from app.modules.usage.contracts.execution import (
    UsageExecutionContext,
    current_usage_context,
    record_agent_run_usage,
)
from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunUsage

logger = get_logger(__name__)


class MeteredModel(WrapperModel):
    """Records a usage row for every request made through the wrapped model."""

    def __init__(
        self,
        wrapped: Model,
        *,
        runtime_profile: dict[str, object | None] | None,
        source_type: str,
    ) -> None:
        super().__init__(wrapped)
        self._runtime_profile = runtime_profile
        self._source_type = source_type

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(
            messages, model_settings, model_request_parameters
        )
        await self._record(response)
        return response

    async def _record(self, response: ModelResponse) -> None:
        ctx = self._usage_context()
        if ctx is None:
            # Nothing to attribute this to. Only reachable outside an agent run,
            # which for compaction means never.
            return
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        try:
            await record_agent_run_usage(
                ctx=ctx,
                runtime_profile=self._runtime_profile,
                usage_data=AgentRunUsage(
                    model_name=self.model_name,
                    usage_kind="llm",
                    input_tokens=_count(usage, "input_tokens"),
                    output_tokens=_count(usage, "output_tokens"),
                    request_count=1,
                    metadata={
                        "helper": self._source_type,
                        "cache_read_tokens": _count(usage, "cache_read_tokens"),
                        "cache_write_tokens": _count(usage, "cache_write_tokens"),
                    },
                ),
                status="COMPLETED",
            )
        except SQLAlchemyError:
            # See the module docstring: the tokens are already spent either way,
            # and failing here would lose the compacted history along with them.
            logger.warning(
                "agent.metered_model.usage_record_failed.degraded",
                source_type=self._source_type,
                exc_info=True,
            )

    def _usage_context(self) -> UsageExecutionContext | None:
        current = current_usage_context()
        if current is None:
            return None
        return replace(current, source_type=self._source_type)


def _count(usage: object, field: str) -> int:
    value = getattr(usage, field, 0)
    try:
        return max(0, int(value))
    except TypeError, ValueError:
        return 0


def metered(
    model: object,
    *,
    runtime_profile: dict[str, object | None] | None,
    source_type: str,
) -> object:
    """Wrap ``model`` for metering when it is one, otherwise hand it back.

    ``WrapperModel`` runs ``infer_model`` on what it is given, which rejects
    anything that is not a real model -- including the stand-ins tests hand the
    harness. Something that is not a model makes no provider calls and so has
    nothing to meter, which makes "return it unchanged" both the safe answer and
    the correct one.
    """
    if not isinstance(model, Model):
        return model
    return MeteredModel(model, runtime_profile=runtime_profile, source_type=source_type)
