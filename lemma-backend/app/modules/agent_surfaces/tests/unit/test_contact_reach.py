"""The platform facts a contact's outreach rules are given (``contact_reach``).

The rules themselves are ``contacts``': ``contacts/tests/unit/test_outreach_rules.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.agent_surfaces.services.contact_reach import (
    WHATSAPP_WINDOW_EXPLANATION,
    reach_for,
)
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.contracts.outreach import Address

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def test_whatsapps_window_runs_a_day_from_the_places_last_message():
    address, window = reach_for(
        "WHATSAPP", address="447700900001", last_inbound_at=NOW - timedelta(hours=23)
    )

    assert address == Address(kind=IdentityKind.PHONE, value="447700900001")
    assert window is not None and window.closes_at == NOW + timedelta(hours=1)
    assert window.explanation == WHATSAPP_WINDOW_EXPLANATION
    _, never = reach_for("WHATSAPP", address="447700900001", last_inbound_at=None)
    assert never is not None and never.closes_at is None


@pytest.mark.parametrize("platform", ["RESEND", "TELEGRAM"])
def test_email_and_telegram_have_no_window(platform):
    _, window = reach_for(platform, address="x", last_inbound_at=None)

    assert window is None


def test_a_web_chat_is_reached_by_no_handle():
    assert reach_for("WEB", address=None, last_inbound_at=None) is None
