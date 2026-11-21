"""Decisions answered by a decision API over HTTP: one request, every question.

Typesafe System One and Google's Decisions API differ in where they live, how
they are signed in to and their accent on the wire (`decision_wire`); the rest
-- metering, timeouts, telling a bad credential from an outage -- is this.

Each call is metered under the caller's execution like a model request, priced
per input token when a price is configured. The credential goes into a header
and nowhere else. Nothing about the evidence or the answer is logged: a refusal
logs its status code, which is all an operator needs to tell a bad credential
from a provider outage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from functools import lru_cache

import httpx

from app.core.log.log import get_logger
from app.modules.decisions.domain.answers import DecisionResult, DecisionUsage
from app.modules.decisions.domain.errors import (
    DecisionUnavailableError,
    images_unsupported,
)
from app.modules.decisions.domain.request import DecisionCaller, DecisionTask
from app.modules.decisions.infrastructure.providers.decision_wire import (
    WireAnswer,
    WireAnswerError,
    WireDialect,
    WireResponse,
    answers_from,
    build_request,
    read_wire,
    split_request,
)
from app.modules.usage.contracts import MeteredRequest

logger = get_logger(__name__)

MeterOpener = Callable[
    [Mapping[str, object]], AbstractAsyncContextManager[MeteredRequest]
]

#: A refusal: the provider looked at the request and did not run it, so it
#: costs nothing. Anything else that is not a 200 may or may not have been
#: billed, and is recorded as unconfirmed.
_REFUSED = frozenset({400, 401, 402, 403, 404, 409, 413, 422, 429})
_CONFIGURATION = frozenset({401, 402, 403, 404})


class WireDecisionProvider:
    """A decision API reached over HTTP. Subclasses say where and how."""

    #: The provider's name, as `DECISION_PROVIDER` spells it.
    provider_name: str
    dialect: WireDialect

    def __init__(
        self,
        *,
        url: str,
        model: str,
        price_per_million_input_tokens_usd: float | None = None,
        client: httpx.AsyncClient | None = None,
        meter: MeterOpener | None = None,
    ) -> None:
        self._url = url
        self._model = model
        self._client = client
        self._meter = meter
        if price_per_million_input_tokens_usd is not None:
            _register_price(self._priced_name(), price_per_million_input_tokens_usd)

    @property
    def name(self) -> str:
        return self.provider_name

    async def _headers(self) -> dict[str, str]:
        """The headers that sign a request in."""
        raise NotImplementedError

    async def decide(
        self, task: DecisionTask, *, caller: DecisionCaller, timeout_seconds: float
    ) -> DecisionResult:
        del caller  # metered through the execution the service opened
        if task.images:
            # Neither decision API takes images as a stable feature yet (System
            # One only behind a preview header): refused before asking, not
            # discovered as a provider refusal.
            raise images_unsupported()
        request = build_request(task, model=self._model, dialect=self.dialect)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        found: dict[str, WireAnswer] = {}
        model: str | None = None
        input_tokens: int | None = None
        for part in split_request(request, self.dialect.max_questions):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise DecisionUnavailableError("timeout")
            wire = await self._ask(part.body, remaining)
            # Only what this part asked: an answer to another part's question
            # must neither fill nor overwrite it.
            found.update(
                (key, answer)
                for key, answer in wire.answers.items()
                if key in part.questions
            )
            model = wire.model or model
            if wire.usage and wire.usage.input_tokens is not None:
                input_tokens = (input_tokens or 0) + wire.usage.input_tokens
        try:
            answers = answers_from(found, task, request.questions)
        except WireAnswerError as exc:
            # The provider's text is not logged: it is its reading of a
            # caller's evidence.
            logger.warning(
                "decisions.wire_provider.answer_unreadable.degraded",
                provider=self.name,
                reason=str(exc),
            )
            raise DecisionUnavailableError("invalid_output") from exc
        return DecisionResult(
            answers=answers,
            provider=self.name,
            model=model or self._model,
            usage=DecisionUsage(input_tokens=input_tokens),
        )

    async def _ask(self, body: object, timeout_seconds: float) -> WireResponse:
        """One request, metered on its own: what it reports is settled before
        anything is made of its answers."""
        async with self._metered() as metered:
            response = await self._post(body, timeout_seconds)
            if response.status_code != 200:
                if response.status_code in _REFUSED:
                    metered.reject()
                self._log_refusal(response.status_code)
                raise DecisionUnavailableError("provider_error")
            try:
                wire = read_wire(response.content)
            except WireAnswerError as exc:
                logger.warning(
                    "decisions.wire_provider.answer_unreadable.degraded",
                    provider=self.name,
                    reason=str(exc),
                )
                raise DecisionUnavailableError("invalid_output") from exc
            if wire.usage and wire.usage.input_tokens is not None:
                metered.settle(input_tokens=wire.usage.input_tokens)
            return wire

    async def _post(self, body: object, timeout_seconds: float) -> httpx.Response:
        from app.core.net.http_client import get_shared_http_client

        client = self._client or get_shared_http_client()
        try:
            return await client.post(
                self._url,
                json=body,
                headers=await self._headers(),
                timeout=httpx.Timeout(
                    timeout_seconds, connect=min(3.0, timeout_seconds), pool=2.0
                ),
            )
        except httpx.TimeoutException as exc:
            raise DecisionUnavailableError("timeout") from exc
        except httpx.TransportError as exc:
            logger.warning(
                "decisions.wire_provider.unreachable.degraded",
                provider=self.name,
                error_type=type(exc).__name__,
            )
            raise DecisionUnavailableError("transport") from exc

    def _metered(self) -> AbstractAsyncContextManager[MeteredRequest]:
        profile = {
            "profile_id": self.name,
            "scope": "SYSTEM",
            "model_name": self._priced_name(),
            "provider_model_name": self._model,
        }
        if self._meter is not None:
            return self._meter(profile)
        return _usage_metered(profile)

    def _priced_name(self) -> str:
        """The name the price is registered and recorded under.

        Prefixed so it can never collide with a language model's own price
        entry.
        """
        return f"{self.name}:{self._model}"

    def _log_refusal(self, status_code: int) -> None:
        if status_code in _CONFIGURATION:
            logger.error(
                "decisions.wire_provider.credential_refused.degraded",
                provider=self.name,
                status_code=status_code,
            )
        else:
            logger.warning(
                "decisions.wire_provider.refused.degraded",
                provider=self.name,
                status_code=status_code,
            )


def _usage_metered(
    profile: Mapping[str, object],
) -> AbstractAsyncContextManager[MeteredRequest]:
    from app.modules.usage.contracts.metering import metered_request

    return metered_request(profile, source="decision")


@lru_cache(maxsize=8)
def _register_price(name: str, per_million_input_usd: float) -> None:
    from app.modules.usage.contracts import ModelPricing
    from app.modules.usage.contracts.execution import UsageService

    UsageService.register_model_pricing(
        {name: ModelPricing(per_million_input_usd, 0.0)}
    )
