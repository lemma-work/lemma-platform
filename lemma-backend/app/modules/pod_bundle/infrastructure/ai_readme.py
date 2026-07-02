"""Optional AI polish for a bundle's README.

Best-effort and degrade-first: publishing must never fail because the polish
model is slow, unavailable, or errors. When ``ai_readme`` is requested we attempt
a single system-model rewrite; any problem falls back to the deterministic
README from :mod:`readme`.

The model call is injected (``polish_fn``) so the publish job can wire the
metered system model and tests can supply a fake; when no runner is available
this returns the input unchanged.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.log.log import get_logger

logger = get_logger(__name__)

PolishFn = Callable[[str], Awaitable[str]]

_PROMPT = (
    "Polish this project README for a shared Lemma pod. Keep every install "
    "badge, link, and heading intact; improve only wording and flow. Return "
    "Markdown only."
)


async def polish_readme(readme: str, *, polish_fn: PolishFn | None = None) -> str:
    if polish_fn is None:
        return readme
    try:
        polished = await polish_fn(readme)
    except Exception as exc:  # noqa: BLE001 - never fail a publish over polish
        logger.warning("README AI polish failed; using deterministic README: %s", exc)
        return readme
    polished = (polished or "").strip()
    # A model that returns nothing or drops the install badge is not trusted.
    if not polished or "img.shields.io" not in polished:
        return readme
    return polished
