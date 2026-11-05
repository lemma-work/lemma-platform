"""WhatsApp accepts a send and reports later that it never arrived.

Outside the 24-hour customer-service window Meta answers 200 and drops the
message, then says so in a ``statuses`` webhook (131047). A proactive send is
refused up front, and a send the status reports as failed -- found by the id
its delivery recorded -- stops being recorded as sent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4


from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.platforms.whatsapp import service as whatsapp_service
from app.modules.agent_surfaces.platforms.whatsapp.parser import failed_deliveries
from app.modules.agent_surfaces.services import whatsapp_delivery_status
from app.modules.agent_surfaces.services.member_reach import MemberReach
from app.modules.agent_surfaces.services.notification_delivery import (
    UndeliverableReason,
)
from app.modules.agent_surfaces.tests.unit.surface_doubles import AnsweredAt, answer_at
from app.modules.test_support.channel_seats_double import InMemoryChannelSeats


def _statuses(*statuses: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {"value": {"statuses": list(statuses)}, "field": "messages"}
                ],
            }
        ],
    }


_OUTSIDE_WINDOW = {
    "id": "wamid-out-1",
    "status": "failed",
    "recipient_id": "15550001",
    "errors": [{"code": 131047, "title": "Re-engagement message"}],
}


def test_a_failed_status_is_read_and_the_others_are_not():
    payload = _statuses(
        _OUTSIDE_WINDOW,
        {"id": "wamid-ok", "status": "delivered", "recipient_id": "15550001"},
    )

    [failure] = failed_deliveries(payload)

    assert (failure.message_id, failure.code) == ("wamid-out-1", "131047")


async def test_a_send_meta_reports_undelivered_is_marked_by_its_id(monkeypatch):
    """The status names Meta's message id; the delivery that carried it, which
    recorded that id when Meta answered 200, is marked DEAD with the reason --
    that delivery, by its own id."""
    marked: list[dict[str, Any]] = []
    carried = SimpleNamespace(id=uuid4())
    looked_up: list[dict[str, Any]] = []

    async def delivery_by_external(_uow, **kwargs):
        looked_up.append(kwargs)
        return carried

    async def delivery_undelivered(_uow, delivery_id, **kwargs) -> bool:
        marked.append({"delivery_id": delivery_id, **kwargs})
        return True

    monkeypatch.setattr(
        whatsapp_delivery_status, "delivery_by_external", delivery_by_external
    )
    monkeypatch.setattr(
        whatsapp_delivery_status, "delivery_undelivered", delivery_undelivered
    )

    await whatsapp_delivery_status.apply_delivery_statuses(
        SimpleNamespace(session=SimpleNamespace(info={})),  # type: ignore[arg-type]
        SurfacePlatformWebhookIngress(
            source="whatsapp", payload=_statuses(_OUTSIDE_WINDOW)
        ),
    )

    [asked] = looked_up
    assert (asked["external_message_id"], asked["platform"]) == (
        _OUTSIDE_WINDOW["id"],
        "WHATSAPP",
    )
    [failed] = marked
    assert failed["delivery_id"] == carried.id
    assert "24 hours" in failed["reason"]


async def test_a_send_returns_the_id_meta_gave_its_first_part():
    service = whatsapp_service.WhatsAppPlatformService(
        {"access_token": "t", "phone_number_id": "pn-1"}
    )
    service._client = SimpleNamespace(
        send_message_payload=AsyncMock(side_effect=["wamid-77", "wamid-78"])
    )
    event = SimpleNamespace(
        platform="WHATSAPP",
        reply_target={"phone_number_id": "pn-1", "sender_wa_id": "15550001"},
        sender_phone="15550001",
        is_dm=True,
    )

    sent = await service.send_message(event, "word " * 1000)  # type: ignore[arg-type]

    assert sent == "wamid-77"


async def test_a_proactive_send_outside_the_window_is_refused_not_dropped():
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="whatsapp",
        surface_type=SurfacePlatform.WHATSAPP,
        config=SurfaceConfig(),
    )
    link = AnsweredAt(
        surface_id=surface.id,
        conversation_id=uuid4(),
        platform="WHATSAPP",
        external_channel_id="pn-1",
        external_thread_id="15550001@pn-1",
        external_user_id="15550001",
        last_event={"sender_external_user_id": "15550001"},
        last_inbound_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    seats = InMemoryChannelSeats()
    answer_at(seats, link, [surface])
    egress = SimpleNamespace(send_agent_message_for_conversation=AsyncMock())
    reach = MemberReach(
        egress=egress,  # type: ignore[arg-type]
        pod_membership_port=SimpleNamespace(  # type: ignore[arg-type]
            get_user_pod_ids=AsyncMock(return_value=[surface.pod_id])
        ),
        external_user_repository=SimpleNamespace(  # type: ignore[arg-type]
            list_by_resolved_users=AsyncMock(
                return_value=[
                    SimpleNamespace(external_user_id="15550001", tenant_id=None)
                ]
            )
        ),
        seats=seats,
    )

    refusal = await reach.send_to_member(
        surface=surface, user_id=uuid4(), message="Your report is ready."
    )

    assert refusal == UndeliverableReason.REPLY_WINDOW_CLOSED
    egress.send_agent_message_for_conversation.assert_not_awaited()
