"""Model work that bills for itself.

Metering used to be something a caller remembered. Five places -- conversation
titles, the schedule filter, README polish, the vision delegate, history
compaction -- each wrote the same twenty lines around a bare `Agent(model).run()`:
reserve against the spend counters, run, extract usage from the result, record
it, settle the reservation. Every one of them obtained its model from the same
factory, so the discipline could have been structural, and instead it was a
convention that the sixth helper would not know about.

`billed` makes it structural. A caller asks for a model *for a purpose*, and
what comes back has already been admitted and will account for itself:

    async with billed(model, source_type="vision", runtime_profile=p, context=c) as m:
        result = await Agent(m).run(prompt)

Admission happens on the way in, so an exhausted allowance refuses before the
provider is called. Recording happens per request, so a caller that never
reaches a terminal result still pays for what it bought. Settlement happens on
the way out however the block is left, so a reservation cannot outlive the work
it was taken for.

Wrapping the *model* rather than each caller is what makes this safe for code we
do not own: history compaction's summarizer builds its own `Agent` internally
and threads no usage anywhere -- `summarization_model`'s docstring says as much,
"one that Lemma never metered" -- and wrapping the model it is handed meters it
without touching it.

A storage fault in the recording never fails the work. A compaction that
succeeded and then could not write its usage row must still return the compacted
history; refusing it would turn a metering problem into a broken conversation.
The catch names the faults that can actually happen on that path rather than
everything, because a bug in the recording code should still be a crash somebody
sees.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import replace

from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunUsage
from app.modules.usage.contracts import UsageReservation
from app.modules.usage.contracts.execution import (
    UsageExecutionContext,
    current_usage_context,
    record_agent_run_usage,
    release_usage_reservation,
    reserve_usage_for_runtime,
)

logger = get_logger(__name__)

#: The three collaborators this module reaches for, as parameters rather than
#: module names a test replaces. Each is resolved at call time from the default
#: below, because a default argument binds at import and would freeze the name
#: past anything that replaces it later.
Recorder = Callable[..., Awaitable[None]]
#: `billed` itself, as a caller injects it. A single seam rather than the
#: reserve/record pair it replaced: admission and recording are the same
#: operation seen from two ends, and injecting them separately let a test
#: arrange a reservation that nothing would ever settle.
MeteringScope = Callable[..., AbstractAsyncContextManager[Model]]
Reserver = Callable[..., Awaitable[UsageReservation | None]]
Releaser = Callable[[UsageReservation | None], Awaitable[None]]


class MeteredModel(WrapperModel):
    """Records a usage row for every request made through the wrapped model.

    Both request paths are overridden. Only `request` was, because compaction is
    the case this started as and the summarizer does not stream -- but "the
    caller happens not to stream" is not a property a metering seam should
    depend on, and a streaming helper would have been silently free.
    """

    def __init__(
        self,
        wrapped: Model,
        *,
        runtime_profile: Mapping[str, object] | None,
        source_type: str,
        context: UsageExecutionContext | None = None,
        reservation: UsageReservation | None = None,
        record: Recorder | None = None,
        release: Releaser | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._record_usage = record or record_agent_run_usage
        self._release_usage = release or release_usage_reservation
        self._runtime_profile = dict(runtime_profile) if runtime_profile else None
        self._source_type = source_type
        self._context = context
        # Consumed by the first request that records, then dropped. A helper
        # makes one model call, so this is the same settlement the hand-rolled
        # versions performed -- but a helper that makes two now settles once and
        # records twice, rather than double-settling.
        self._reservation = reservation

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

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        # Deliberately unannotated: the base declares it `RunContext[Any] | None`
        # and pyright infers that here, where writing it out would add an `Any`
        # to a module that has none. Narrowing it instead is not open to us --
        # an override may not tighten a parameter type.
        run_context=None,
    ) -> AsyncIterator[StreamedResponse]:
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream
            # After the stream is exhausted, which is the first moment its usage
            # is final. Inside the `async with` so a caller that abandons the
            # stream part-way still settles on the way out of the block.
            await self._record(stream)

    async def settle(self) -> None:
        """Give back a reservation no request ever consumed.

        The zero-request case is real: a helper whose model refuses before the
        first call, or whose caller raised between admission and use. Left
        alone the hold survives until the whole window rolls over, quietly
        shrinking that person's allowance in the meantime.
        """
        reservation, self._reservation = self._reservation, None
        if reservation is not None:
            await self._release_usage(reservation)

    async def _record(self, source: object) -> None:
        ctx = self._context or self._usage_context()
        if ctx is None:
            # Nothing to attribute this to. Reachable only outside an agent run
            # and without an explicit context -- which for every caller today
            # means never, and for the next one means an unattributed row is
            # not written rather than a wrong one being written.
            return
        usage = getattr(source, "usage", None)
        if usage is None:
            return
        reservation, self._reservation = self._reservation, None
        try:
            await self._record_usage(
                ctx=replace(ctx, source_type=self._source_type),
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
                reservation=reservation,
            )
        except SQLAlchemyError, OSError, ValidationError:
            # See the module docstring: the tokens are already spent either way,
            # and failing here would lose the work along with them. `OSError`
            # because a database that has gone away surfaces as a socket error
            # before SQLAlchemy has anything to wrap, and `ValidationError`
            # because the numbers come from a provider and a shape we have never
            # seen must not end the conversation.
            logger.warning(
                "agent.metered_model.usage_record_failed.degraded",
                source_type=self._source_type,
                exc_info=True,
            )

    def _usage_context(self) -> UsageExecutionContext | None:
        """The run this is happening inside, when there is one.

        Compaction runs within the agent run that triggered it, so the ambient
        context is the right attribution and puts the compaction row next to the
        run that caused it. The helpers that run outside any run pass their own.
        """
        return current_usage_context()


def _count(usage: object, field: str) -> int:
    value = getattr(usage, field, 0)
    try:
        return max(0, int(value))
    except TypeError, ValueError:
        return 0


def metered(
    model: object,
    *,
    runtime_profile: Mapping[str, object] | None,
    source_type: str,
) -> object:
    """Wrap ``model`` for metering when it is one, otherwise hand it back.

    ``WrapperModel`` runs ``infer_model`` on what it is given, which rejects
    anything that is not a real model -- including the stand-ins tests hand the
    harness. Something that is not a model makes no provider calls and so has
    nothing to meter, which makes "return it unchanged" both the safe answer and
    the correct one.

    No reservation: this is for work that happens *inside* a run already
    admitted against the same allowance. Use ``billed`` for work that starts on
    its own.
    """
    if not isinstance(model, Model):
        return model
    return MeteredModel(model, runtime_profile=runtime_profile, source_type=source_type)


@asynccontextmanager
async def billed(
    model: Model,
    *,
    source_type: str,
    runtime_profile: Mapping[str, object] | None,
    context: UsageExecutionContext,
    reserve: Reserver | None = None,
    record: Recorder | None = None,
    release: Releaser | None = None,
) -> AsyncIterator[Model]:
    """``model``, admitted against the caller's allowance and accounting for itself.

    Raises ``UsageLimitExceededError`` on the way in when the allowance is
    already spent -- before the provider is called, which is the only point at
    which refusing is free. Callers that are tools rather than runs translate
    that into their own failure so one refused helper does not end a whole run.

    The reservation is settled on the way out however the block is left: by the
    request that recorded, or by ``settle`` when none did.
    """
    reservation = await (reserve or reserve_usage_for_runtime)(
        organization_id=context.organization_id,
        user_id=context.user_id,
        runtime_profile=dict(runtime_profile) if runtime_profile else None,
    )
    wrapped = MeteredModel(
        model,
        runtime_profile=runtime_profile,
        source_type=source_type,
        context=context,
        reservation=reservation,
        record=record,
        release=release,
    )
    try:
        yield wrapped
    finally:
        await wrapped.settle()
