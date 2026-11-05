"""Which provider answers decisions on this server.

One per deployment, chosen by `DECISION_PROVIDER`, the way the system model is
chosen by its own settings. Adding a provider is an adapter that answers every
question kind, and a line below. There is no fallback from one to another when
a provider fails: it is reported as unavailable, so a caller retries the same
judgement rather than silently getting a different one.

Choosing is another matter. Typesafe chosen without its key is a deployment
that cannot use it at all, not a provider that failed, so the deployment's own
language model (`DECISION_MODEL`, else its default) answers instead, and a
warning says so once. Decisions -- and the schedule filters and workflow steps
that ask them -- keep working wherever a model does.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import lru_cache
from types import MappingProxyType

from app.core.config import reveal_secret
from app.core.log.log import get_logger
from app.modules.decisions.config import DecisionsSettings
from app.modules.decisions.domain.errors import DecisionUnavailableError
from app.modules.decisions.domain.ports import DecisionProvider

logger = get_logger(__name__)


def _model(settings: DecisionsSettings) -> DecisionProvider:
    from app.modules.decisions.infrastructure.providers.model_provider import (
        ModelDecisionProvider,
    )

    return ModelDecisionProvider(model_name=settings.decision_model or None)


def _typesafe(settings: DecisionsSettings) -> DecisionProvider:
    api_key = reveal_secret(settings.typesafe_api_key)
    if not api_key:
        _warn_typesafe_unconfigured()
        return _model(settings)
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


@lru_cache(maxsize=1)
def _warn_typesafe_unconfigured() -> None:
    """Once per process: the setting is read on every decision."""
    logger.warning("decisions.registry.typesafe_unconfigured.degraded")


def build_provider(settings: DecisionsSettings) -> DecisionProvider:
    factory = PROVIDERS.get(settings.decision_provider)
    if factory is None:
        raise DecisionUnavailableError(
            "not_configured",
            f"Unknown DECISION_PROVIDER {settings.decision_provider!r}.",
        )
    return factory(settings)
