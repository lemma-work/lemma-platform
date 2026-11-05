"""E2E for what Resend reports after it took a channel's email.

A permanent bounce or a spam complaint names the email by Resend's id, which
the line's delivery recorded when it was sent: the delivery is given up, and
the contact's address is suppressed in the pod, so the next line to them is
skipped rather than sent. A temporary bounce changes nothing, and an email
that is not one of the channels' deliveries is ignored.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as core_settings
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_resend_svix_headers,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_channel_deliveries_e2e import (
    _contact_channel,
    _deliver,
    _row,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import CUSTOMER
from app.modules.channels.contracts import DeliveryStatus
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.domain.handles import handle_hash
from app.modules.contacts.infrastructure.models import ContactSuppressionModel

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

_SECRET = "whsec_" + base64.b64encode(b"resend-delivery-events-key").decode()


async def _resend_says(
    client: AsyncClient, monkeypatch, event: dict[str, Any]
) -> dict[str, str]:
    monkeypatch.setattr(core_settings, "resend_webhook_secret", _SECRET)
    body = json.dumps(event).encode()
    response = await client.post(
        "/surfaces/webhooks/resend",
        content=body,
        headers=build_resend_svix_headers(raw_body=body, signing_secret=_SECRET),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _event(kind: str, email_id: str, **data: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "created_at": "2026-10-19T08:00:00.000Z",
        "data": {"email_id": email_id, "to": [CUSTOMER], **data},
    }


async def _a_sent_line(
    client, db_session, *, test_pod, fixed_test_user, fake_resend, monkeypatch
):
    pod_id = test_pod["id"]
    channel = await _contact_channel(
        client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    written = await member_writes(client, pod_id, channel, "Your order shipped.")
    [delivery] = await deliveries_of_line(written["id"])
    await _deliver(delivery.id)
    sent = await _row(delivery.id)
    assert sent.status is DeliveryStatus.SENT and sent.external_message_id
    return channel, sent


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("email.bounced", {"bounce": {"type": "Permanent", "message": "No such user"}}),
        ("email.complained", {}),
    ],
)
async def test_a_bounce_or_complaint_gives_up_the_delivery_and_stops_the_next(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
    kind: str,
    extra: dict[str, Any],
):
    channel, sent = await _a_sent_line(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )

    said = await _resend_says(
        authenticated_client,
        monkeypatch,
        _event(kind, sent.external_message_id, **extra),
    )

    assert said == {"message": "Recorded"}
    dead = await _row(sent.id)
    assert dead.status is DeliveryStatus.DEAD and dead.reason
    again = await member_writes(
        authenticated_client, test_pod["id"], channel, "Did it arrive?"
    )
    [next_delivery] = await deliveries_of_line(again["id"])
    await _deliver(next_delivery.id)
    assert (await _row(next_delivery.id)).status is DeliveryStatus.SKIPPED


async def test_a_temporary_bounce_or_somebody_elses_email_changes_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    _channel, sent = await _a_sent_line(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )

    soft = await _resend_says(
        authenticated_client,
        monkeypatch,
        _event("email.bounced", sent.external_message_id, bounce={"type": "Temporary"}),
    )
    unknown = await _resend_says(
        authenticated_client,
        monkeypatch,
        _event("email.bounced", "not-one-of-ours", bounce={"type": "Permanent"}),
    )

    assert soft == unknown == {"message": "Ignored: nothing to record"}
    assert (await _row(sent.id)).status is DeliveryStatus.SENT


async def test_a_bounce_suppresses_the_address_it_was_for_not_everyone_copied(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The email also went to somebody copied on her thread: the bounce is
    about the delivery's place -- her address -- and nobody else is stopped."""
    _channel, sent = await _a_sent_line(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    copied = "colleague@customer.example.test"
    event = _event(
        "email.bounced", sent.external_message_id, bounce={"type": "Permanent"}
    )
    event["data"]["to"] = [CUSTOMER, copied]

    await _resend_says(authenticated_client, monkeypatch, event)

    db_session.expire_all()
    suppressed = set(
        (
            await db_session.scalars(
                select(ContactSuppressionModel.value_hash).where(
                    ContactSuppressionModel.pod_id == UUID(test_pod["id"])
                )
            )
        ).all()
    )
    assert handle_hash(IdentityKind.EMAIL, CUSTOMER) in suppressed
    assert handle_hash(IdentityKind.EMAIL, copied) not in suppressed


async def test_a_bounce_resend_could_not_classify_is_not_permanent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    _channel, sent = await _a_sent_line(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )

    said = await _resend_says(
        authenticated_client,
        monkeypatch,
        _event(
            "email.bounced", sent.external_message_id, bounce={"type": "Undetermined"}
        ),
    )

    assert said == {"message": "Ignored: nothing to record"}
    assert (await _row(sent.id)).status is DeliveryStatus.SENT
