"""A WhatsApp send reported failed later: read, and logged where it can be seen."""

from __future__ import annotations

import logging


import pytest

from app.modules.agent_surfaces.platforms.whatsapp.statuses import (
    parse_failed_whatsapp_statuses,
)
from app.modules.agent_surfaces.services import delivery_statuses
from app.modules.agent_surfaces.tests.e2e.platform_payloads import whatsapp

pytestmark = pytest.mark.asyncio


async def test_only_failed_statuses_are_read():
    body = whatsapp.status_failed(131047, "wamid.out-1")
    body["entry"][0]["changes"][0]["value"]["statuses"].append(
        {"id": "wamid.out-2", "status": "delivered", "recipient_id": "1"}
    )

    (failure,) = parse_failed_whatsapp_statuses(body)

    assert failure.external_message_id == "wamid.out-1"
    assert failure.code == "131047"
    assert failure.title == "Re-engagement message"
    assert failure.recipient == whatsapp.SENDER_PHONE
    assert failure.sender_id == whatsapp.PHONE_NUMBER_ID


async def test_a_message_delivery_carries_no_statuses():
    assert parse_failed_whatsapp_statuses(whatsapp.text()) == []
    assert parse_failed_whatsapp_statuses({"entry": "junk"}) == []


def test_each_failure_in_a_webhook_is_logged(caplog):
    caplog.set_level(logging.WARNING)

    count = delivery_statuses.log_delivery_statuses(
        whatsapp.status_failed(131026, "wamid.out-1"), source="whatsapp"
    )

    assert count == 1
    logged = [
        record.getMessage()
        for record in caplog.records
        if "delivery_statuses.send_failed" in record.getMessage()
    ]
    assert len(logged) == 1
    assert "131026" in logged[0]
    # The recipient's number never reaches the log.
    assert whatsapp.SENDER_PHONE not in logged[0]


def test_a_message_delivery_logs_nothing():
    assert (
        delivery_statuses.log_delivery_statuses(whatsapp.text(), source="whatsapp") == 0
    )
