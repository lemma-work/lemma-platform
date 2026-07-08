"""Resolve the settings ``.env`` path — disabled under the test harness.

Tests must be deterministic and self-contained: they set the config they need
(via fixtures / conftest / field defaults), NOT a developer's local ``.env``.
A leaked ``.env`` makes the suite non-deterministic — a value like ``API_URL`` or
``ENABLE_TELEGRAM_POLLING_MODE`` present locally but absent in CI flips tests.

The root test ``conftest`` sets ``LEMMA_DISABLE_DOTENV=1`` so every ``Settings``
class skips the dev ``.env`` and runs against defaults + explicit overrides,
matching CI (which has no ``.env``). The ``.env`` is still honored in
real-LLM / real-sandbox e2e mode, where it legitimately supplies real API keys.
"""

from __future__ import annotations

import os


def dotenv_path() -> str | None:
    """``.env`` for a normal process; ``None`` when the test harness disabled it."""
    if os.getenv("LEMMA_DISABLE_DOTENV") == "1":
        return None
    return ".env"
