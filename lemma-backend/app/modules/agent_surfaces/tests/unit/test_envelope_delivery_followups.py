"""What ``deliver_envelope`` does around the platform call.

A direct chat whose reply window has closed is not tried at all; one still open
is delivered on the chat.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.envelope import (
    DeliveryReceipt,
    PartDelivery,
    SurfaceEnvelope,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery

pytestmark = pytest.mark.asyncio


class _Session:
    def in_transaction(self) -> bool:
        return False


def _delivery(**seams) -> SurfaceDelivery:
    return SurfaceDelivery(
        uow=SimpleNamespace(session=_Session()),
        surface_repository=AsyncMock(),
        conversation_link_repository=AsyncMock(),
        adapter_registry=SimpleNamespace(),
        credential_resolver=AsyncMock(),
        **seams,
    )


def _target(adapter, *, hours_since_inbound: float):
    return SimpleNamespace(
        adapter=adapter,
        credentials={},
        event=SimpleNamespace(is_dm=True),
        link=SimpleNamespace(
            conversation_kind="DM",
            inbound_activity_at=datetime.now(timezone.utc)
            - timedelta(hours=hours_since_inbound),
            external_user_id="447700900123",
        ),
        surface=SimpleNamespace(id=uuid4(), surface_type=SurfacePlatform.WHATSAPP),
        conversation_user_id=uuid4(),
    )


async def test_a_closed_window_is_not_tried_on_the_chat():
    adapter = SimpleNamespace(deliver=AsyncMock())
    emailed = AsyncMock(return_value=SurfaceDeliveryResult.by_email_because("x"))

    result = await _delivery(after_the_window=emailed).deliver_envelope(
        _target(adapter, hours_since_inbound=25),
        envelope=SurfaceEnvelope(text="late answer"),
        metadata={},
        conversation_id=uuid4(),
    )

    assert result.by_email
    adapter.deliver.assert_not_awaited()


async def test_an_open_window_is_delivered_on_the_chat():
    adapter = SimpleNamespace(
        deliver=AsyncMock(
            return_value=DeliveryReceipt(parts={"text": PartDelivery.NATIVE})
        )
    )

    result = await _delivery().deliver_envelope(
        _target(adapter, hours_since_inbound=1),
        envelope=SurfaceEnvelope(text="on time"),
        metadata={},
        conversation_id=uuid4(),
    )

    assert result and result.channel == "chat"
    adapter.deliver.assert_awaited_once()
