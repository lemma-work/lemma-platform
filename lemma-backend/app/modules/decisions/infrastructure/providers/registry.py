"""Which provider answers decisions on this server.

One per deployment, chosen by `DECISION_PROVIDER`, the way the system model is
chosen by its own settings. Adding a provider is an adapter that answers every
question kind, and a line below. There is no fallback from one to another: a
provider that fails is reported as unavailable, so a caller retries the same
judgement rather than silently getting a different one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from app.core.config import reveal_secret
from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.ports import DecisionProvider


def _model(settings: DecisionsSettings) -> DecisionProvider:
    from app.modules.decisions.infrastructure.providers.model_provider import (
        ModelDecisionProvider,
    )

    return ModelDecisionProvider(model_name=settings.decision_model or None)


def _typesafe(settings: DecisionsSettings) -> DecisionProvider:
    api_key = reveal_secret(settings.typesafe_api_key)
    if not api_key:
        raise DecisionUnavailableError(
            "not_configured",
            "DECISION_PROVIDER is typesafe but TYPESAFE_API_KEY is not set.",
        )
    from app.modules.decisions.infrastructure.providers.typesafe_provider import (
        TypesafeDecisionProvider,
    )

    return TypesafeDecisionProvider(
        api_key=api_key,
        base_url=settings.typesafe_base_url,
        model=settings.typesafe_model,
        price_per_million_input_tokens_usd=(
            settings.typesafe_price_per_million_input_tokens_usd
        ),
    )


PROVIDERS: Mapping[str, Callable[[DecisionsSettings], DecisionProvider]] = (
    MappingProxyType({"model": _model, "typesafe": _typesafe})
)


def build_provider(settings: DecisionsSettings) -> DecisionProvider:
    factory = PROVIDERS.get(settings.decision_provider)
    if factory is None:
        raise DecisionUnavailableError(
            "not_configured",
            f"Unknown DECISION_PROVIDER {settings.decision_provider!r}.",
        )
    return factory(settings)
