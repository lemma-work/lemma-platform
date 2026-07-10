"""Optional AI polish for a bundle's README.

Best-effort and degrade-first: publishing must never fail because the polish
model is slow, unavailable, or errors. When ``ai_readme`` is requested we attempt
a single system-model rewrite; any problem falls back to the deterministic
README from :mod:`readme`.

The model call is injected (``polish_fn``) so tests can supply a fake; the publish
job wires the metered system model via :func:`build_system_polish_fn`. When no
``polish_fn`` is given this returns the input unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.log.log import get_logger

logger = get_logger(__name__)

PolishFn = Callable[[str], Awaitable[str]]

_PROMPT = (
    "You are polishing the README landing page for a shared Lemma pod (a "
    "published GitHub repo someone can one-click install). Improve the wording, "
    "flow, and the tagline so it reads like an inviting product page, and you may "
    "add a short intro sentence or lightly reorganize prose.\n\n"
    "Hard constraints — do NOT change these:\n"
    "- Keep the centered install button exactly as-is: the `<a>`/`<img>` block "
    "whose image is the `https://img.shields.io/...Install%20to%20Lemma...` badge.\n"
    "- Keep every link, the `<div align=\"center\">` header, the '## Install' "
    "instructions, and the resource counts in 'What's inside'.\n"
    "- Do not invent resources, features, or links that are not already present.\n"
    "Return only the Markdown, no code fences, no commentary."
)


async def polish_readme(readme: str, *, polish_fn: PolishFn | None = None) -> str:
    if polish_fn is None:
        return readme
    try:
        polished = await polish_fn(readme)
    except Exception as exc:  # noqa: BLE001 - never fail a publish over polish
        logger.warning("README AI polish failed; using deterministic README: %s", exc)
        return readme
    polished = _strip_code_fence((polished or "").strip())
    # A model that returns nothing or drops the install badge is not trusted.
    if not polished or "img.shields.io" not in polished:
        return readme
    return polished


def _strip_code_fence(text: str) -> str:
    """Peel a wrapping ```markdown … ``` fence a model sometimes adds."""
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            return "\n".join(lines).strip()
    return text


def build_system_polish_fn(
    *,
    user_id: UUID,
    organization_id: UUID | None,
    pod_id: UUID,
) -> PolishFn:
    """A ``polish_fn`` that rewrites the README with the metered **system** model,
    mirroring ``ConversationTitleService`` (resolve the system runtime → run a
    one-shot pydantic-ai agent → record usage). Any failure propagates so
    :func:`polish_readme` falls back to the deterministic README."""

    async def _polish(readme: str) -> str:
        from pydantic_ai import Agent as PydanticAIAgent, UsageLimits

        from app.core.domain.runtime import AgentRuntimeConfig
        from app.composition.pod_bundle_readme import (
            require_pydantic_ai_model_from_runtime_profile,
        )
        from app.composition.pod_bundle_readme import (
            DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
            AgentRuntimeProfileService,
        )
        from app.composition.pod_bundle_readme import (
            record_pydantic_ai_result_usage,
            reserve_usage_for_runtime,
        )
        from app.composition.pod_bundle_readme import UsageExecutionContext

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
        usage_context = UsageExecutionContext(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            source_type="pod_bundle_readme",
        )
        reservation = await reserve_usage_for_runtime(
            organization_id=organization_id,
            user_id=user_id,
            runtime_profile=runtime_profile,
        )
        agent = PydanticAIAgent(model, system_prompt=_PROMPT)
        result = None
        try:
            result = await agent.run(
                readme,
                usage_limits=UsageLimits(
                    request_limit=1,
                    input_tokens_limit=64_000,
                    output_tokens_limit=8_000,
                    total_tokens_limit=72_000,
                    count_tokens_before_request=True,
                ),
            )
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
                result=result,
                status="COMPLETED",
                reservation=reservation,
                metadata={"helper": "pod_bundle_readme"},
            )
        except Exception:
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
                result=result,
                status="FAILED",
                reservation=reservation,
                metadata={"helper": "pod_bundle_readme"},
            )
            raise
        return str(result.output)

    return _polish
