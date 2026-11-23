from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.identity.domain.email import (
    is_apple_private_relay,
    normalize_identity_email,
)


def test_normalize_identity_email_lowercases_and_trims():
    assert normalize_identity_email("  User.Name+Tag@Example.COM  ") == (
        "user.name+tag@example.com"
    )


def test_normalize_identity_email_rejects_malformed_email():
    with pytest.raises(ValidationError):
        normalize_identity_email("not-an-email")


def test_is_apple_private_relay_matches_only_apples_relay_domain():
    assert is_apple_private_relay("abc123@privaterelay.appleid.com")
    assert is_apple_private_relay("ABC123@PrivateRelay.AppleID.com")
    assert not is_apple_private_relay("someone@icloud.com")
    assert not is_apple_private_relay("someone@appleid.com")
    assert not is_apple_private_relay("someone@evil-privaterelay.appleid.com")
    assert not is_apple_private_relay("privaterelay.appleid.com@example.com")
