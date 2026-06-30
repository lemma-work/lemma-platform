"""Helpers for tests that exercise the system:lemma LLM provider.

Usage
-----
At module level in a test file::

    pytestmark = [..., pytest.mark.skipif(
        not system_lemma_available(),
        reason=SYSTEM_LEMMA_SKIP_REASON,
    )]

Or inside a test::

    def test_something():
        skip_unless_system_lemma()
        ...

The worker fixture automatically receives the resolved LEMMA_OPENAI_* vars
via ``system_lemma_env_overlay()`` — no extra wiring needed in individual tests.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[5]


@lru_cache(maxsize=1)
def _dotenv_vars() -> dict[str, str]:
    """Read the backend .env file without modifying os.environ."""
    try:
        from dotenv import dotenv_values

        env_path = _backend_root() / ".env"
        if not env_path.exists():
            return {}
        return {k: v for k, v in dotenv_values(env_path).items() if v is not None}
    except ImportError:
        return {}


def system_lemma_env_overlay() -> dict[str, str]:
    """LEMMA_OPENAI_* vars for the worker subprocess.

    os.environ takes precedence over .env (allows CI override).
    """
    base = _dotenv_vars()
    overlay: dict[str, str] = {}
    # Merge .env LEMMA_* vars first
    for key, value in base.items():
        if key.startswith("LEMMA_"):
            overlay[key] = value
    # os.environ takes precedence
    for key, value in os.environ.items():
        if key.startswith("LEMMA_"):
            overlay[key] = value
    return overlay


def system_lemma_api_key() -> str | None:
    env = system_lemma_env_overlay()
    return env.get("LEMMA_OPENAI_API_KEY") or None


def system_lemma_default_model() -> str:
    """The configured default model for system:lemma (from .env or os.environ).

    Falls back to the app's own config default (``settings.lemma_openai_default_model``)
    rather than a hard-coded literal, so the helper can never drift from what the
    running app resolves when no LEMMA_OPENAI_* env is set.
    """
    from app.core.config import settings

    env = system_lemma_env_overlay()
    return env.get("LEMMA_OPENAI_DEFAULT_MODEL") or settings.lemma_openai_default_model


def system_lemma_model_names() -> list[str]:
    """All configured model names for system:lemma."""
    from app.core.config import settings

    env = system_lemma_env_overlay()
    raw = env.get("LEMMA_OPENAI_MODEL_NAMES") or settings.lemma_openai_model_names
    models = [m.strip() for m in raw.split(",") if m.strip()]
    default = system_lemma_default_model()
    if default and default not in models:
        models.insert(0, default)
    return models


def e2e_real_llm() -> bool:
    """True when e2e should hit the REAL model (needs a key); False = mock.

    Default is mock (fast/CI). ``E2E_LLM_MODE`` wins if set; otherwise ``E2E_REAL``.
    Evaluated from the env so it works at collection time (before fixtures run).
    """
    mode = os.environ.get("E2E_LLM_MODE", "").lower()
    if mode == "real":
        return True
    if mode == "mock":
        return False
    return os.environ.get("E2E_REAL", "").lower() in ("1", "true", "yes")


def system_lemma_available() -> bool:
    """True if the agent LLM can run.

    In mock mode (the default) the deterministic FunctionModel needs no key, so
    this is always True. In real mode it requires LEMMA_OPENAI_API_KEY.
    """
    if not e2e_real_llm():
        return True
    return bool(system_lemma_api_key())


SYSTEM_LEMMA_SKIP_REASON = (
    "LEMMA_OPENAI_API_KEY is not configured — "
    "add it to lemma-backend/.env or set it as an environment variable "
    "to run system:lemma provider tests."
)


def skip_unless_system_lemma() -> None:
    """Call inside a test to skip it if system:lemma is not configured."""
    import pytest

    if not system_lemma_available():
        pytest.skip(SYSTEM_LEMMA_SKIP_REASON)
