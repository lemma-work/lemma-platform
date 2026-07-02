"""Optional AI polish pass over the generated README draft.

Publish must never depend on a model being configured or reachable, so this is
strictly best-effort: any missing profile, mock/e2e mode, timeout, provider
error, exhausted usage quota, or a response that dropped the badge/most of the
document falls back to the deterministic draft from readme.py (the caller
treats ``None`` as "use the draft"). The model only gets to improve prose —
every factual section is already correct in the draft, and the post-check
below rejects output that lost the one thing the README exists for (the
import badge).
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.pod_import.infrastructure.readme import IMPORT_BADGE_URL

logger = get_logger(__name__)

_POLISH_TIMEOUT_SECONDS = 30.0

_POLISH_SYSTEM_PROMPT = (
    "You are polishing a README for a shareable automation pod. Improve the "
    "prose — the title tagline and the 'What it does' section — for clarity "
    "and appeal. Keep ALL markdown tables, the import badge line, the section "
    "structure, and every factual claim EXACTLY unchanged. Output only the "
    "final markdown document; do not wrap it in a code fence."
)


def polish_available() -> bool:
    """True when publish will attempt the system-LLM polish pass. Mock/e2e mode
    counts as off (the scripted FunctionModel would mangle the README, not
    polish it), and a *misconfigured* profile — key set but no models, which
    makes ``_system_lemma_profile`` raise instead of returning None — reads as
    unavailable rather than an error: preview/publish must never fail over it."""
    from app.modules.agent.infrastructure.harnesses.mock_model import is_mock_llm_enabled
    from app.modules.agent.services.runtime_profile_service import _system_lemma_profile

    if is_mock_llm_enabled():
        return False
    try:
        return _system_lemma_profile() is not None
    except Exception as exc:
        logger.warning("System model profile unavailable for README polish: %s", exc)
        return False


async def polish_readme(
    draft: str,
    *,
    pod_name: str,
    description: str | None,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID,
) -> str | None:
    """Return the polished README, or ``None`` when the caller should keep the draft."""
    if not polish_available():
        return None
    try:
        polished = await asyncio.wait_for(
            _polish(
                draft,
                pod_name=pod_name,
                description=description,
                user_id=user_id,
                organization_id=organization_id,
                pod_id=pod_id,
            ),
            timeout=_POLISH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # best-effort: the draft is always publishable
        logger.warning("README polish failed for %s, keeping the draft: %s", pod_name, exc)
        return None
    polished = _strip_code_fence(polished)
    # A polish that lost the badge or most of the document isn't a polish —
    # e.g. the model replied with commentary instead of the full markdown.
    if IMPORT_BADGE_URL not in polished or len(polished) < len(draft) // 2:
        return None
    return polished


def _strip_code_fence(text: str) -> str:
    """Unwrap a whole-document ``` fence — the one instruction models most
    often ignore. Left wrapped, GitHub would render the entire README as a
    single literal code block (dead badge included)."""
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


async def _resolve_system_model(*, user_id: UUID, organization_id: UUID | None):
    """Resolve the system profile the same way conversation titling does,
    returning ``(model, runtime_profile_snapshot)`` — the snapshot is what the
    usage layer needs to reserve against and bill the run."""
    from app.modules.agent.domain.value_objects import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeConfig,
    )
    from app.modules.agent.services.runtime_model_factory import (
        require_pydantic_ai_model_from_runtime_profile,
    )
    from app.modules.agent.services.runtime_profile_service import AgentRuntimeProfileService

    resolved = await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID),
        organization_id=organization_id,
        user_id=user_id,
    )
    runtime_profile = resolved.public_snapshot()
    model = require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=resolved.credentials or {},
        fallback_model_name=resolved.model_name_for_harness,
    )
    return model, runtime_profile


async def _polish(
    draft: str,
    *,
    pod_name: str,
    description: str | None,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID,
) -> str:
    from pydantic_ai import Agent as PydanticAIAgent

    from app.modules.usage.services.pydantic_ai_tracking import (
        record_pydantic_ai_result_usage,
        reserve_usage_for_runtime,
    )
    from app.modules.usage.services.usage_context import UsageExecutionContext

    model, runtime_profile = await _resolve_system_model(
        user_id=user_id, organization_id=organization_id
    )
    context = f"Pod name: {pod_name}." + (f" Pod description: {description}" if description else "")
    agent = PydanticAIAgent(model, system_prompt=f"{_POLISH_SYSTEM_PROMPT}\n\n{context}")

    # Metered like every other direct system-model run (conversation titles,
    # schedule filters): reservation enforces the org's usage limit up front —
    # an exhausted quota raises here and the caller falls back to the draft —
    # and the recording bills the tokens whether the run completed or failed.
    usage_context = UsageExecutionContext(
        user_id=user_id,
        organization_id=organization_id,
        pod_id=pod_id,
        source_type="readme_polish",
    )
    reservation = await reserve_usage_for_runtime(
        organization_id=organization_id,
        user_id=user_id,
        runtime_profile=runtime_profile,
    )
    result = None
    try:
        result = await agent.run(draft)
        await record_pydantic_ai_result_usage(
            ctx=usage_context,
            runtime_profile=runtime_profile,
            result=result,
            status="COMPLETED",
            reservation=reservation,
            metadata={"helper": "readme_polish"},
        )
    except BaseException:
        # BaseException, not Exception: the caller's asyncio.wait_for cancels
        # this coroutine on timeout, and a bare `except Exception` would let
        # CancelledError skip the settle — leaving the reservation held
        # forever against the org's usage limit.
        await record_pydantic_ai_result_usage(
            ctx=usage_context,
            runtime_profile=runtime_profile,
            result=result,
            status="FAILED",
            reservation=reservation,
            metadata={"helper": "readme_polish"},
        )
        raise

    return str(result.output).strip()
