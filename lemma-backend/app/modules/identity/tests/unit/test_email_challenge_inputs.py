from __future__ import annotations

import pytest

from app.modules.identity.domain.email_challenge import (
    parse_code_reply,
    parse_email_reply,
)
from app.modules.agent_surfaces.config import SurfaceSettings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ada@acme.com!", "ada@acme.com"),
        ("My email is <Ada@Acme.com>.", "ada@acme.com"),
        ("ada+onboarding@acme.com", "ada+onboarding@acme.com"),
        ("a@acme.com b@acme.com", None),
        ("ada@acme", None),
        ("thanks", None),
    ],
)
def test_typed_email_is_canonical_and_unambiguous(
    message: str, expected: str | None
) -> None:
    assert parse_email_reply(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("thanks", None),
        ("please", None),
        ("my code is 012345.", "012345"),
        ("1234567", None),
        ("a123456", None),
        ("123456 or 654321", None),
        ("１２３４５６", None),
    ],
)
def test_only_one_six_digit_code_spends_an_attempt(
    message: str, expected: str | None
) -> None:
    assert parse_code_reply(message) == expected


@pytest.mark.parametrize("ttl_seconds", [0, -1])
def test_explicit_invalid_onboarding_ttl_is_rejected(ttl_seconds: int) -> None:
    with pytest.raises(ValueError):
        SurfaceSettings(surface_onboarding_ttl_seconds=ttl_seconds)
