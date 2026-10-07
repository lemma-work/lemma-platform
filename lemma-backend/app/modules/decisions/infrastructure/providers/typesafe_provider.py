"""Decisions answered by Typesafe System One, over HTTP.

One request per decision, every question in it. Each call is metered under the
caller's execution like a model request, priced per input token when
`TYPESAFE_PRICE_PER_MILLION_INPUT_TOKENS_USD` is set.

The key goes into the Authorization header and nowhere else. Nothing about the
evidence or the answer is logged: a refusal logs its status code, which is all
an operator needs to tell a bad key from a provider outage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from functools import lru_cache

import httpx

from app.core.log.log import get_logger
from app.modules.decisions.domain.answers import DecisionResult, DecisionUsage
from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.request import DecisionCaller, DecisionTask
from app.modules.decisions.infrastructure.providers.typesafe_wire import (
    WireAnswerError,
    build_request,
    parse_response,
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


class TypesafeDecisionProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        price_per_million_input_tokens_usd: float | None = None,
        client: httpx.AsyncClient | None = None,
        meter: MeterOpener | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/systemone"
        self._model = model
        self._client = client
        self._meter = meter
        if price_per_million_input_tokens_usd is not None:
            _register_price(_priced_name(model), price_per_million_input_tokens_usd)

    @property
    def name(self) -> str:
        return "typesafe"

    async def decide(
        self, task: DecisionTask, *, caller: DecisionCaller, timeout_seconds: float
    ) -> DecisionResult:
        del caller  # metered through the execution the service opened
        request = build_request(task, model=self._model)
        async with self._metered() as metered:
            response = await self._post(request.body, timeout_seconds)
            if response.status_code != 200:
                if response.status_code in _REFUSED:
                    metered.reject()
                _log_refusal(response.status_code)
                raise DecisionUnavailableError("provider_error")
            try:
                answers, model, input_tokens = parse_response(
                    response.content, task, request.questions
                )
            except WireAnswerError as exc:
                logger.warning(
                    "decisions.typesafe_provider.answer_unreadable.degraded",
                    exc_info=True,
                )
                raise DecisionUnavailableError("invalid_output") from exc
            if input_tokens is not None:
                metered.settle(input_tokens=input_tokens)
        return DecisionResult(
            answers=answers,
            provider=self.name,
            model=model or self._model,
            usage=DecisionUsage(input_tokens=input_tokens),
        )

    async def _post(self, body: object, timeout_seconds: float) -> httpx.Response:
        from app.core.net.http_client import get_shared_http_client

        client = self._client or get_shared_http_client()
        try:
            return await client.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=httpx.Timeout(
                    timeout_seconds, connect=min(3.0, timeout_seconds), pool=2.0
                ),
            )
        except httpx.TimeoutException as exc:
            raise DecisionUnavailableError("timeout") from exc
        except httpx.TransportError as exc:
            logger.warning(
                "decisions.typesafe_provider.unreachable.degraded",
                error_type=type(exc).__name__,
            )
            raise DecisionUnavailableError("transport") from exc

    def _metered(self) -> AbstractAsyncContextManager[MeteredRequest]:
        profile = {
            "profile_id": "typesafe",
            "scope": "SYSTEM",
            "model_name": _priced_name(self._model),
            "provider_model_name": self._model,
        }
        if self._meter is not None:
            return self._meter(profile)
        return _usage_metered(profile)


def _usage_metered(
    profile: Mapping[str, object],
) -> AbstractAsyncContextManager[MeteredRequest]:
    from app.modules.usage.contracts.metering import metered_request

    return metered_request(profile, source="decision")


def _priced_name(model: str) -> str:
    """The name System One's price is registered and recorded under.

    Prefixed so it can never collide with a language model's own price entry.
    """
    return f"typesafe:{model}"


@lru_cache(maxsize=8)
def _register_price(name: str, per_million_input_usd: float) -> None:
    from app.modules.usage.contracts import ModelPricing
    from app.modules.usage.contracts.execution import UsageService

    UsageService.register_model_pricing(
        {name: ModelPricing(per_million_input_usd, 0.0)}
    )


def _log_refusal(status_code: int) -> None:
    if status_code in _CONFIGURATION:
        logger.error(
            "decisions.typesafe_provider.key_refused.degraded", status_code=status_code
        )
    else:
        logger.warning(
            "decisions.typesafe_provider.refused.degraded", status_code=status_code
        )
