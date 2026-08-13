"""A mismatched pydantic-ai must stop the build, not degrade it.

Dev shipped an image whose application code was written against pydantic-ai 2.x
while 1.107 was installed. The check in place then logged a warning and let the
process come up, so the service served traffic on an agent stack whose public
API it was not written for — and the failure surfaced as unexplained behaviour in
users' conversations rather than as a container that would not start.
"""

from __future__ import annotations

import pytest

from app.modules.agent.infrastructure import pydantic_ai_compat

pytestmark = pytest.mark.unit


def test_a_major_mismatch_refuses_to_load() -> None:
    with pytest.raises(pydantic_ai_compat.UnsupportedPydanticAIVersion) as caught:
        pydantic_ai_compat.check_pydantic_ai_version("1.107.0")

    # Both numbers, because the fix depends on knowing which way they diverged.
    assert "1.107.0" in str(caught.value)
    assert str(pydantic_ai_compat.SUPPORTED_PYDANTIC_AI_MAJOR) in str(caught.value)


def test_minor_and_patch_drift_is_allowed() -> None:
    """The upper bound in pyproject governs here; the private imports above
    would fail on their own if a name actually moved."""
    for version in ("2.0.0", "2.27.1", "2.99.3"):
        pydantic_ai_compat.check_pydantic_ai_version(version)


def test_a_non_numeric_version_is_not_treated_as_a_mismatch() -> None:
    """An editable install off a branch reports something like `2.28.0.dev`."""
    pydantic_ai_compat.check_pydantic_ai_version("main")


def test_the_installed_version_satisfies_the_check() -> None:
    """The import-time call already ran; this states the invariant plainly."""
    pydantic_ai_compat.check_pydantic_ai_version()
