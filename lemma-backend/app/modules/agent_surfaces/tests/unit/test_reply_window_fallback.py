"""A reply the chat will no longer take: emailed to its own user, or refused."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.models import (
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.services import reply_window_fallback
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    UndeliverableReason,
)

pytestmark = pytest.mark.asyncio

OWNER = uuid4()


def _target(
    *,
    hours_since_inbound: float,
    is_dm: bool = True,
    kind: str = "DM",
    platform: SurfacePlatform = SurfacePlatform.WHATSAPP,
    sender: str = "447700900123",
):
    return SimpleNamespace(
        event=SimpleNamespace(is_dm=is_dm),
        link=SimpleNamespace(
            conversation_kind=kind,
            inbound_activity_at=datetime.now(timezone.utc)
            - timedelta(hours=hours_since_inbound),
            external_user_id=sender,
            routed_agent_id=None,
        ),
        surface=SimpleNamespace(surface_type=platform, agent_id=uuid4()),
        pod_id=uuid4(),
        conversation_user_id=OWNER,
    )


@pytest.mark.parametrize(
    "target, closed",
    [
        (_target(hours_since_inbound=1), False),
        (_target(hours_since_inbound=25), True),
        # Groups are left alone: the window there is unconfirmed.
        (_target(hours_since_inbound=25, is_dm=False, kind="CHANNEL"), False),
        # Only WhatsApp has a window.
        (_target(hours_since_inbound=200, platform=SurfacePlatform.TELEGRAM), False),
    ],
)
async def test_only_a_direct_whatsapp_chat_past_a_day_is_closed(target, closed):
    assert reply_window_fallback.reply_window_closed(target) is closed


def _ports(*, resolved_user_id, channels=(), **seams):
    return reply_window_fallback.EmailFallbackPorts(
        identities=SimpleNamespace(
            get_by_identity=AsyncMock(
                return_value=SimpleNamespace(resolved_user_id=resolved_user_id)
            )
        ),
        channels=SimpleNamespace(resolve=AsyncMock(return_value=(list(channels), ""))),
        credentials=SimpleNamespace(for_surface=AsyncMock(return_value={})),
        links=None,
        adapters=SimpleNamespace(get=lambda _platform: None),
        **seams,
    )


async def test_a_stranger_in_the_chat_is_not_emailed_the_owners_reply():
    result = await reply_window_fallback.deliver_after_the_window(
        SimpleNamespace(),
        _target(hours_since_inbound=30),
        envelope=SurfaceEnvelope(text="hi"),
        metadata={},
        conversation_id=uuid4(),
        ports=_ports(resolved_user_id=uuid4()),
    )

    assert not result
    assert result.reason == UndeliverableReason.REPLY_WINDOW_CLOSED


async def test_the_owner_gets_it_by_email_and_a_question_is_answered_by_reply():
    mailbox = SimpleNamespace(
        id=uuid4(),
        surface_type=SurfacePlatform.RESEND,
        is_active=True,
        surface_identity_email="scout@pods.example.com",
    )
    opened = AsyncMock(return_value=SimpleNamespace())
    typed = AsyncMock()
    ports = _ports(
        resolved_user_id=OWNER,
        channels=[DeliveryChannel(surface=mailbox, email_address="ada@example.com")],
        open_thread=opened,
        remember_thread=AsyncMock(),
        remember_typed=typed,
    )
    conversation_id = uuid4()
    question = SurfaceQuestionRenderPlan(
        title="Pick",
        questions=[SurfaceQuestion(header="Size", question="Which size?", options=[])],
        callback_id=f"{conversation_id}|call-7",
    )

    result = await reply_window_fallback.deliver_after_the_window(
        SimpleNamespace(session=SimpleNamespace(in_transaction=lambda: False)),
        _target(hours_since_inbound=30),
        envelope=SurfaceEnvelope(text="Here it is.", choices=question),
        metadata={"agent_display_name": "Scout"},
        conversation_id=conversation_id,
        ports=ports,
    )

    assert result.by_email
    sent = opened.await_args.kwargs
    assert sent["recipient_email"] == "ada@example.com"
    assert sent["subject"] == "Scout: a reply you missed on WhatsApp"
    assert "Here it is." in sent["message"]
    assert "Which size?" in sent["message"]
    assert ports.channels.resolve.await_args.kwargs["channel"] == "email"
    assert typed.await_args.kwargs["tool_call_id"] == "call-7"
