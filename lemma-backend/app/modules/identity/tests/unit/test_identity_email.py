from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.identity.domain.email import normalize_identity_email


def test_normalize_identity_email_lowercases_and_trims():
    assert normalize_identity_email("  User.Name+Tag@Example.COM  ") == (
        "user.name+tag@example.com"
    )


def test_normalize_identity_email_rejects_malformed_email():
    with pytest.raises(ValidationError):
        normalize_identity_email("not-an-email")
