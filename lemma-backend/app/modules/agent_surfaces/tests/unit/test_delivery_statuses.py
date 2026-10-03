"""A WhatsApp send reported failed later: read, traced, and acted on once."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.domain.outbound_messages import SurfaceOutboundMessage
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


def _sent(kind: str, **fields) -> SurfaceOutboundMessage:
    defaults = {
        "id": uuid4(),
        "surface_id": uuid4(),
        "conversation_id": uuid4(),
        "notification_id": None,
        "platform": "WHATSAPP",
        "external_message_id": "wamid.out-1",
        "recipient": whatsapp.SENDER_PHONE,
        "kind": kind,
        "body": "The report is ready.",
        "status": "SENT",
        "error": None,
    }
    return SurfaceOutboundMessage(**{**defaults, **fields})


#: The WhatsApp adapter's half, whichever adapter class ends up carrying it.
_WHATSAPP_STATUSES = SimpleNamespace(
    get=lambda _platform: SimpleNamespace(
        parse_delivery_statuses=parse_failed_whatsapp_statuses
    )
)


class _Fixture:
    def __init__(self, sent, *, first_report: bool = True, **seams):
        self.repository = SimpleNamespace(
            get_by_external_id=AsyncMock(return_value=sent),
            mark_failed=AsyncMock(return_value=first_report),
        )
        self.uow = SimpleNamespace(session=object(), commit=AsyncMock())
        self.emailed = seams.pop("after_the_window", AsyncMock())
        self.notice = AsyncMock()

        @asynccontextmanager
        async def uows():
            yield self.uow

        self.handler = delivery_statuses.DeliveryStatusHandler(
            uows,
            adapters=_WHATSAPP_STATUSES,
            outbound=lambda _session: self.repository,
            after_the_window=self.emailed,
            append_notice=self.notice,
            **seams,
        )

    async def apply(self, code: int = 131047) -> int:
        return await self.handler.apply(
            whatsapp.status_failed(code, "wamid.out-1"), source="whatsapp"
        )


async def test_a_reply_outside_the_window_goes_by_email_and_the_agent_is_told():
    target = SimpleNamespace()
    delivery = SimpleNamespace(
        resolve_egress_target=AsyncMock(return_value=target),
        egress_metadata=AsyncMock(return_value={}),
    )
    fixture = _Fixture(
        _sent("REPLY"),
        surface_delivery=lambda _uow: delivery,
        after_the_window=AsyncMock(
            return_value=SurfaceDeliveryResult.by_email_because("closed")
        ),
    )

    assert await fixture.apply() == 1

    assert fixture.emailed.await_args.kwargs["envelope"].text == "The report is ready."
    assert "sent to their email address" in fixture.notice.await_args.kwargs["notice"]


async def test_another_failure_tells_the_agent_without_rerouting():
    fixture = _Fixture(_sent("REPLY"))

    await fixture.apply(code=131000)

    fixture.emailed.assert_not_awaited()
    assert "They have not seen it" in fixture.notice.await_args.kwargs["notice"]


async def test_a_notification_is_delivered_again_anywhere_but_here():
    notification = SimpleNamespace(id=uuid4())
    service = SimpleNamespace(
        deliver=AsyncMock(return_value=SimpleNamespace(delivery_status="DELIVERED"))
    )
    fixture = _Fixture(
        _sent("NOTIFICATION", notification_id=notification.id),
        notifications=lambda _uow: SimpleNamespace(
            get=AsyncMock(return_value=notification)
        ),
        notification_service=lambda _uow: service,
    )

    await fixture.apply()

    assert service.deliver.await_args.kwargs["exclude_platform"].value == "WHATSAPP"


@pytest.mark.parametrize(
    "sent, first_report",
    [
        (None, True),  # not ours on record
        (_sent("REPLY"), False),  # already acted on
        (_sent("REPLY_PART"), True),  # one chunk of a reply its head answers for
    ],
)
async def test_nothing_is_done_twice_or_for_strangers(sent, first_report):
    fixture = _Fixture(sent, first_report=first_report)

    await fixture.apply()

    fixture.emailed.assert_not_awaited()
    fixture.notice.assert_not_awaited()
