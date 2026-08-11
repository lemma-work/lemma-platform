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
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings

logger = get_logger(__name__)


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


async def resolve_summarization_model(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    fallback: object,
) -> object:
    """A pydantic-ai model for compaction, or ``fallback`` (the run's model)."""
    model_name = configured_summarization_model_name()
    if not model_name:
        return fallback

    # Imported here: this module is pulled in by the harness, and the profile
    # service reaches back into services that import the harness.
    from app.modules.agent.domain.runtime_profiles import AgentRuntimeConfig
    from app.modules.agent.services.runtime_model_factory import (
        pydantic_ai_model_from_runtime_profile,
    )
    from app.modules.agent.services.runtime_profile_service import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeProfileService,
    )

    try:
        resolved = await AgentRuntimeProfileService().resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=model_name,
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
        model = pydantic_ai_model_from_runtime_profile(
            runtime_profile=resolved.public_snapshot(),
            runtime_credentials=resolved.credentials,
        )
    except Exception:
        logger.debug(
            "agent.summarization_model.resolution_failed.diagnostic",
            exc_info=True,
        )
        return fallback
    return model or fallback
