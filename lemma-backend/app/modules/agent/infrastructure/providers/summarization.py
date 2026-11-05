"""Resolve the model that compacts conversation history.

Compaction defaulted to the run's own model, so every compaction was a ~70k-token
request on the most expensive model in play — and, because the summarizer builds
a bare `Agent` internally, one that Lemma never metered. Pointing this at a small
fast model is usually strictly better: summarising a transcript is not the task
the expensive model was chosen for.

Falls back to the run's model when nothing is configured or the configured name
is not in this deployment's catalog, so a bad value degrades to today's
behaviour instead of breaking every long conversation.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from pydantic_ai.models import Model

from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.runtime_choice import RuntimePurpose
from app.modules.agent.domain.value_objects import JsonObject

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CompactionTarget:
    """The model a run's history is summarized on, and the settings to send it."""

    model: Model | str
    model_settings: JsonObject | None = None


class LazyCompactionTarget:
    """A compaction target resolved the first time a run actually compacts.

    Resolving it reads the runtime profile, and most runs never compact: they
    finish well inside the window. So the read happens on first use, once per
    run, rather than at the start of every run.
    """

    def __init__(self, resolve: Callable[[], Awaitable[CompactionTarget]]) -> None:
        self._resolve = resolve
        self._target: CompactionTarget | None = None

    async def get(self) -> CompactionTarget:
        if self._target is None:
            self._target = await self._resolve()
        return self._target


#: What a compactor may be handed: a model outright, or a target resolved later.
type SummarizationModel = Model | str | LazyCompactionTarget


def lazy_compaction_target(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    run_model: Model,
    run_compaction_settings: JsonObject | None,
    meter: Callable[[Model | str], Model | str],
    resolve_model: Callable[..., Awaitable[Model | str]] | None = None,
) -> LazyCompactionTarget:
    """The run's compaction target, resolved only if the run ever compacts.

    The run's own model gets the compaction settings resolved with the run's
    generation settings (``generation_settings.run_generation_settings``): its
    own parameters, turned down to a light effort and a summary-sized cap. A
    separately configured summarization model gets only the summary cap, since
    nothing says which of the run's settings it would accept.

    ``resolve_model`` picks the model; ``resolve_summarization_model`` unless
    a caller supplies another.
    """
    from app.modules.agent.services.run_input_settings import summary_only_settings
    from app.modules.agent.services.run_phase_spans import run_phase

    async def resolve() -> CompactionTarget:
        with run_phase("summarization_model"):
            model = await (resolve_model or resolve_summarization_model)(
                organization_id=organization_id, user_id=user_id, fallback=run_model
            )
        settings = run_compaction_settings if model is run_model else None
        return CompactionTarget(
            meter(model), settings if settings is not None else summary_only_settings()
        )

    return LazyCompactionTarget(resolve)


def configured_summarization_model_name() -> str | None:
    """The configured model name, env taking precedence over settings.

    Matches `runtime_profile_service`'s idiom: live env wins over the cached
    Settings singleton, so an operator can change it without a restart.
    """
    raw = os.getenv("HISTORY_SUMMARIZATION_MODEL")
    if raw is None:
        raw = agent_settings.history_summarization_model
    value = (raw or "").strip()
    return value or None


async def resolve_summarization_model[T](
    *,
    organization_id: UUID | None,
    user_id: UUID,
    fallback: T,
) -> Model | T:
    """A pydantic-ai model for compaction, or ``fallback`` (the run's model).

    Unset, without a system provider, or unresolvable (logged), compaction
    stays on the run's own model.
    """
    # Imported here: this module is pulled in by the harness, and resolution
    # reaches the profile service, which reaches back into services that
    # import the harness.
    from app.modules.agent.services.background_model import BackgroundModel
    from app.modules.agent.services.runtime_resolution import RuntimeRequest

    model = await BackgroundModel().model_or(
        RuntimePurpose.COMPACTION,
        RuntimeRequest(
            organization_id=organization_id,
            user_id=user_id,
            model_name=configured_summarization_model_name(),
        ),
        fallback=fallback,
    )
    return cast(Model | T, model)
