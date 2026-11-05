"""Verification switched off is answered from the flag, before any read.

Every inbound WhatsApp message that looks like a verification code asks whether
verification is configured. With the feature off the answer is no whatever the
number pool holds, so reading the pool first cost a database session per such
message for nothing -- and made a unit test of the webhook need Postgres.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.config import surface_settings
from app.modules.identity.config import identity_settings
from app.modules.identity.contracts.whatsapp_verification import (
    is_whatsapp_verification_configured,
)


def _no_database() -> None:
    raise AssertionError("answering a switched-off feature opened a session")


async def test_verification_switched_off_is_answered_without_reading_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        identity_settings, "auth_whatsapp_mobile_verification_enabled", False
    )
    # Settings incomplete, which is exactly when the pool would be consulted.
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", None)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.contracts.whatsapp.async_session_maker",
        _no_database,
    )

    assert await is_whatsapp_verification_configured() is False
