"""Which provider answers decisions on this server.

One per deployment, chosen by `DECISION_PROVIDER`, the way the system model is
chosen by its own settings. Adding a provider is an adapter that answers every
question kind, and a line below. There is no fallback from one to another when
a provider fails: it is reported as unavailable, so a caller retries the same
judgement rather than silently getting a different one.

Choosing is another matter. Gemini or Typesafe chosen without its key is a
deployment that cannot use it at all, not a provider that failed, so the
deployment's own language model (`DECISION_MODEL`, else its default) answers
instead, and the log says so once. Decisions -- and the schedule filters and
workflow steps that ask them -- keep working wherever a model does. Gemini is
the default, so a deployment that never chose it is told at info, not warned.
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


def _gemini(settings: DecisionsSettings) -> DecisionProvider:
    api_key = reveal_secret(settings.lemma_google_api_key)
    if not api_key:
        _note_gemini_unconfigured(
            chosen="decision_provider" in settings.model_fields_set
        )
        return _model(settings)
    from app.modules.decisions.infrastructure.providers.gemini_provider import (
        GeminiDecisionProvider,
    )

    return GeminiDecisionProvider(
        api_key=api_key,
        base_url=settings.gemini_decision_base_url,
        model=settings.gemini_decision_model,
        price_per_million_input_tokens_usd=(
            settings.gemini_decision_price_per_million_input_tokens_usd
        ),
    )


PROVIDERS: Mapping[str, Callable[[DecisionsSettings], DecisionProvider]] = (
    MappingProxyType({"gemini": _gemini, "model": _model, "typesafe": _typesafe})
)


@lru_cache(maxsize=2)
def _note_gemini_unconfigured(*, chosen: bool) -> None:
    """Once per process: the setting is read on every decision."""
    if chosen:
        logger.warning("decisions.registry.gemini_unconfigured.degraded")
    else:
        logger.info("decisions.registry.gemini_unconfigured.observed")


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
