"""Every provider dispatch spends from its execution's local allocation."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.usage.infrastructure.price_catalog import RateCard
from contextlib import asynccontextmanager

import anyio
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from app.modules.usage.domain.accounting import TokenCounts
from app.modules.usage.domain.errors import UsageContextMissingError
from app.modules.usage.services.metering_scope import current_metering_scope


def _counts(usage: RequestUsage) -> TokenCounts:
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
    ) -> None:
        super().__init__(wrapped)
        self.runtime_profile = dict(profile)
        self.source = source

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with self._dispatch(model_settings, model_request_parameters) as dispatch:
            response = await self.wrapped.request(
                messages, dispatch.settings, model_request_parameters
            )
            dispatch.usage = response.usage
            return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context=None,
    ) -> AsyncIterator[StreamedResponse]:
        async with self._dispatch(model_settings, model_request_parameters) as dispatch:
            async with self.wrapped.request_stream(
                messages, dispatch.settings, model_request_parameters, run_context
            ) as stream:
                yield stream
            # The adapter has finalized its receipt on context exit. A partial
            # stream has no final receipt and retains its request bound instead.
            dispatch.usage = stream.usage

    @asynccontextmanager
    async def _dispatch(
        self, settings: ModelSettings | None, parameters: ModelRequestParameters
    ) -> AsyncIterator["Dispatch"]:
        scope = current_metering_scope()
        if scope is None:
            raise UsageContextMissingError()
        meter, pricing = scope.meter(self.runtime_profile, self.source)
        effective: ModelSettings = {**(settings or {})}
        output_ceiling = (
            effective.get("max_tokens") or scope.settings.usage_request_output_ceiling
        )
        bound = (
            pricing.bound(output_ceiling)
            if pricing.enforceable and pricing.input_ceiling
            else None
        )
        extras: Mapping[str, object] = effective
        priceable = not parameters.native_tools and not _compound_billing(extras)
        if not priceable:
            bound = None
        ticket = await meter.before(bound)
        if meter.allocation is not None and meter.allocation.limited:
            effective["max_tokens"] = output_ceiling
        dispatch = Dispatch(effective)
        try:
            yield dispatch
        finally:
            with anyio.fail_after(10, shield=True):
                counts = _counts(dispatch.usage) if dispatch.usage is not None else None
                await meter.after(
                    ticket,
                    counts,
                    _price_receipt(pricing, counts, priceable),
                )


class Dispatch:
    def __init__(self, settings: ModelSettings) -> None:
        self.settings = settings
        self.usage: RequestUsage | None = None


def _compound_billing(settings: Mapping[str, object]) -> bool:
    # These provider features can add billable iterations or TTL-dependent
    # charges which the adapter does not expose in its normalized receipt.
    if settings.get("anthropic_context_management") or settings.get(
        "anthropic_advisor"
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
