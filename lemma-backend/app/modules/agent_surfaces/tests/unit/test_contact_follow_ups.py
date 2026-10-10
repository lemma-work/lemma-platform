"""When a pod may write first to a contact, channel by channel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.contact_follow_ups import (
    FollowUpRefused,
    _check_channel,
    _verified_email,
    handle_for_unsubscribe,
    unsubscribe_token,
)
from app.modules.contacts.contracts import (
    ContactHandle,
    IdentityKind,
    IdentityStrength,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def _handle(
    kind: IdentityKind,
    *,
    wrote=None,
    unsubscribed=None,
    strength=IdentityStrength.CHANNEL,
    verified_at=NOW,
) -> ContactHandle:
    return ContactHandle(
        id=uuid4(),
        contact_id=uuid4(),
        kind=kind,
        value="x",
        strength=strength,
        verified_at=verified_at,
        last_inbound_at=wrote,
        unsubscribed_at=unsubscribed,
    )


def test_whatsapp_is_open_for_a_day_after_the_contact_last_wrote():
    recent = _handle(IdentityKind.PHONE, wrote=NOW - timedelta(hours=23))

    assert _check_channel("WHATSAPP", [recent], NOW) == recent
    with pytest.raises(FollowUpRefused) as refused:
        _check_channel(
            "WHATSAPP",
            [_handle(IdentityKind.PHONE, wrote=NOW - timedelta(hours=25))],
            NOW,
        )
    assert refused.value.code == "outside_window"


def test_nobody_is_written_to_where_they_unsubscribed():
    with pytest.raises(FollowUpRefused) as refused:
        _check_channel(
            "RESEND", [_handle(IdentityKind.EMAIL, wrote=NOW, unsubscribed=NOW)], NOW
        )
    assert refused.value.code == "unsubscribed"


def test_a_channel_the_contact_has_no_handle_on_is_refused():
    with pytest.raises(FollowUpRefused):
        _check_channel("TELEGRAM", [_handle(IdentityKind.EMAIL, wrote=NOW)], NOW)


def test_a_web_chat_needs_no_handle():
    assert _check_channel("WEB", [], NOW) is None


def test_an_unsubscribe_link_names_one_handle_and_cannot_be_altered():
    handle = uuid4()
    token = unsubscribe_token(handle)

    assert handle_for_unsubscribe(token) == handle
    assert handle_for_unsubscribe(f"{uuid4()}.{token.split('.', 1)[1]}") is None
    assert handle_for_unsubscribe("junk") is None


def test_email_goes_to_the_address_most_recently_proved():
    older = _handle(IdentityKind.EMAIL, verified_at=NOW - timedelta(days=3))
    coded = _handle(IdentityKind.EMAIL, strength=IdentityStrength.CODE)

    assert _verified_email([older, coded, _handle(IdentityKind.PHONE)]) == coded


def test_an_address_a_member_typed_in_is_not_emailed():
    with pytest.raises(FollowUpRefused) as refused:
        _verified_email([_handle(IdentityKind.EMAIL, strength=IdentityStrength.MEMBER)])
    assert refused.value.code == "no_verified_email"


def test_an_unsubscribed_address_stops_email_to_the_contact():
    with pytest.raises(FollowUpRefused) as refused:
        _verified_email(
            [
                _handle(IdentityKind.EMAIL, strength=IdentityStrength.CODE),
                _handle(IdentityKind.EMAIL, unsubscribed=NOW),
            ]
        )
    assert refused.value.code == "unsubscribed"
