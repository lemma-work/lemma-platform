"""Check shared usage before dispatch and record each actual provider outcome."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.modules.usage.infrastructure.price_catalog import RateCard
from contextlib import asynccontextmanager

import anyio
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage
from pydantic_ai.tools import RunContext

from app.modules.usage.domain.accounting import RequestReceipt, TokenCounts
from app.modules.usage.domain.errors import (
    UsageContextMissingError,
    ProviderAttemptsExhaustedError,
)
from app.modules.usage.infrastructure.provider_retries import (
    MAX_PROVIDER_ATTEMPTS,
    PROVIDER_ERRORS,
    is_harness_owned_drop,
    retry_delay,
    confirmed_rejection,
)
from app.modules.usage.infrastructure.request_features import (
    priceable_text_request,
)
from app.modules.usage.services.metering_scope import current_metering_scope


def _counts(usage: RequestUsage) -> TokenCounts | None:
    # Adapters normalize an absent provider receipt to all-zero usage. A complete
    # response alone cannot prove that a model request consumed no paid tokens.
    if not any(
        (
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            usage.input_audio_tokens,
            usage.output_audio_tokens,
            usage.cache_audio_read_tokens,
        )
    ):
        return None
    return TokenCounts(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        input_audio_tokens=usage.input_audio_tokens,
        output_audio_tokens=usage.output_audio_tokens,
        cache_audio_read_tokens=usage.cache_audio_read_tokens,
        request_count=1,
    )


class MeteredModel(WrapperModel):
    def __init__(
        self,
        wrapped: Model,
        profile: Mapping[str, object],
        *,
        source: str | None = None,
        retry_stream_connections: bool = True,
    ) -> None:
        super().__init__(wrapped)
        self.runtime_profile = dict(profile)
        self.source = source
        self.retry_stream_connections = retry_stream_connections

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        for attempt in range(MAX_PROVIDER_ATTEMPTS):
            dispatch: Dispatch | None = None
            try:
                async with self._dispatch(
                    messages, model_settings, model_request_parameters
                ) as dispatch:
                    response = await self.wrapped.request(
                        messages, dispatch.settings, model_request_parameters
                    )
                    dispatch.responded = True
                    if response.state == "complete":
                        dispatch.usage = response.usage
                    return response
            except PROVIDER_ERRORS as exc:
                if dispatch is None or dispatch.provider_error is not exc:
                    raise
                await _retry_or_raise(exc, attempt)
        raise AssertionError("Provider retry loop ended without a result")

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        for attempt in range(MAX_PROVIDER_ATTEMPTS):
            handed_to_consumer = False
            dispatch: Dispatch | None = None
            try:
                async with self._dispatch(
                    messages, model_settings, model_request_parameters
                ) as dispatch:
                    async with self.wrapped.request_stream(
                        messages,
                        dispatch.settings,
                        model_request_parameters,
                        run_context,
                    ) as stream:
                        handed_to_consumer = True
                        yield stream
                    dispatch.responded = True
                    if stream.get().state == "complete":
                        dispatch.usage = stream.usage
                return
            except PROVIDER_ERRORS as exc:
                # Once the caller has the stream, only the harness can replace its
                # partial output and resume history without repeating tool effects.
                #
                # The same is true before handover for a dropped connection: the
                # harness resumes from recorded messages and emits `stream_reset`
                # so the client discards what it was shown. Retrying here instead
                # is invisible to it, and swaps the original error for
                # `ProviderAttemptsExhaustedError` on the way out -- which is how
                # a dropped connection came to be reported as "try again later".
                # A `ModelHTTPError` still retries here: the provider answered,
                # there is no partial stream, and that is what the `Retry-After`
                # handling is for.
                if (
                    handed_to_consumer
                    or dispatch is None
                    or dispatch.provider_error is not exc
                    or (
                        not self.retry_stream_connections and is_harness_owned_drop(exc)
                    )
                ):
                    raise
                await _retry_or_raise(exc, attempt)

    @asynccontextmanager
    async def _dispatch(
        self,
        messages: list[ModelMessage],
        settings: ModelSettings | None,
        parameters: ModelRequestParameters,
    ) -> AsyncIterator["Dispatch"]:
        scope = current_metering_scope()
        if scope is None:
            raise UsageContextMissingError()
        meter, pricing = scope.meter(self.runtime_profile, self.source)
        effective: ModelSettings = {**(self.wrapped.settings or {}), **(settings or {})}
        output_ceiling = (
            effective.get("max_tokens") or scope.settings.usage_request_output_ceiling
        )
        _, prepared_parameters = self.wrapped.prepare_request(effective, parameters)
        priceable = priceable_text_request(
            messages, prepared_parameters, effective
        ) and not _compound_billing(effective)
        request_id, occurred_at, limited = await meter.before(
            priceable=priceable and pricing.priceable
        )
        if limited:
            effective["max_tokens"] = output_ceiling
        dispatch = Dispatch(effective, request_id, occurred_at)
        try:
            yield dispatch
        except PROVIDER_ERRORS as exc:
            dispatch.provider_error = exc
            dispatch.rejected = confirmed_rejection(exc)
            raise
        finally:
            with anyio.fail_after(10, shield=True):
                receipt = dispatch.receipt(pricing, priceable)
                await meter.after(receipt)
                if limited and dispatch.responded and receipt.cost is None:
                    meter.require_reconciliation = True


class Dispatch:
    def __init__(
        self, settings: ModelSettings, request_id: UUID, occurred_at: datetime
    ) -> None:
        self.settings = settings
        self.request_id = request_id
        self.occurred_at = occurred_at
        self.usage: RequestUsage | None = None
        self.rejected = False
        self.responded = False
        self.provider_error: Exception | None = None

    def receipt(self, pricing: RateCard, priceable: bool) -> RequestReceipt:
        counts = (
            TokenCounts(request_count=1)
            if self.rejected
            else _counts(self.usage)
            if self.usage is not None
            else None
        )
        cost = (
            Decimal(0) if self.rejected else _price_receipt(pricing, counts, priceable)
        )
        if counts is None:
            counts = TokenCounts(request_count=1, unconfirmed_requests=1)
        elif cost is None:
            counts = counts.model_copy(update={"unpriced_requests": 1})
        return RequestReceipt(
            request_id=self.request_id,
            occurred_at=self.occurred_at,
            counts=counts,
            cost=cost,
        )


def _compound_billing(settings: Mapping[str, object]) -> bool:
    # These provider features can add billable iterations or TTL-dependent
    # charges which the adapter does not expose in its normalized receipt.
    if (
        settings.get("extra_body")
        or settings.get("anthropic_context_management")
        or settings.get("anthropic_advisor")
        or settings.get("anthropic_speed") == "fast"
        or settings.get("service_tier") == "priority"
        or settings.get("openai_service_tier") == "priority"
    ):
        return True
    return any(
        value == "1h"
        for key, value in settings.items()
        if key.startswith("anthropic_cache")
    )


def _price_receipt(
    pricing: RateCard, counts: TokenCounts | None, priceable: bool
) -> Decimal | None:
    return pricing.price(counts) if counts is not None and priceable else None


async def _retry_or_raise(exc: Exception, attempt: int) -> None:
    delay = retry_delay(exc, attempt)
    if delay is None:
        raise exc
    if attempt + 1 >= MAX_PROVIDER_ATTEMPTS:
        raise ProviderAttemptsExhaustedError() from exc
    await asyncio.sleep(delay)
