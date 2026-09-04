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
    "whose image is the `https://img.shields.io/...Run%20it%20on%20Lemma...` badge.\n"
    "- Keep the `social-card.png` hero image exactly as-is.\n"
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
    except Exception:  # noqa: BLE001 - never fail a publish over polish
        logger.debug(
            "pod_bundle.ai_readme.readme_ai_polish_using_deterministic.diagnostic"
        )
        return readme
    polished = _strip_code_fence((polished or "").strip())
    if not polished or not _preserves_required_invariants(readme, polished):
        return readme
    return polished


def _preserves_required_invariants(original: str, polished: str) -> bool:
    """Reject model output that drops any executable landing-page structure."""
    required_exact_lines: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith(("<a ", "<img", "!["))
            or stripped == '<div align="center">'
            or stripped.startswith("| ")
            and " **" in stripped
        ):
            required_exact_lines.append(stripped)
    required_markers = [marker for marker in ("## 🚀 Install",) if marker in original]
    preserves_lines = all(line in polished for line in required_exact_lines)
    preserves_markers = all(marker in polished for marker in required_markers)
    preserves_centering = polished.count('<div align="center">') >= original.count(
        '<div align="center">'
    ) and polished.count("</div>") >= original.count("</div>")
    return preserves_lines and preserves_markers and preserves_centering


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

        from app.modules.agent.contracts.metering import billed
        from app.modules.agent.contracts.model_runtime import resolve_system_runtime
        from app.modules.usage.contracts.execution import UsageExecutionContext

        polish_limits = UsageLimits(
            request_limit=1,
            input_tokens_limit=64_000,
            output_tokens_limit=8_000,
            total_tokens_limit=72_000,
            count_tokens_before_request=True,
        )
        runtime = await resolve_system_runtime(
            usage_limits=polish_limits,
            user_id=user_id,
            organization_id=organization_id,
        )
        usage_context = UsageExecutionContext(
            user_id=user_id,
            organization_id=organization_id,
            pod_id=pod_id,
            source_type="pod_bundle_readme",
        )
        async with billed(
            runtime.model,
            source_type="pod_bundle_readme",
            runtime_profile=runtime.runtime_profile,
            context=usage_context,
        ) as metered_polish_model:
            agent = PydanticAIAgent(metered_polish_model, system_prompt=_PROMPT)
            result = await agent.run(readme, usage_limits=runtime.usage_limits)
        return str(result.output)

    return _polish
