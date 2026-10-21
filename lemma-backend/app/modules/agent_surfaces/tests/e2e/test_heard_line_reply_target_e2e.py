"""A heard line keeps how to answer it, never what was said or who said it.

Every line a person says on a platform keeps its reply target
(``channel_messages.reply_ref``): the place, the thread, the message replied
to, the id to answer -- and its place keeps the latest one
(``channel_sources.delivery_ref``). Neither keeps the words, the sender's
number, address or name, or the rest of the platform's event; the line itself
holds the words, once. The answer still reaches the person from it.

Against Postgres, through the WhatsApp webhook and the fake Meta server.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _set_user_mobile_number,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_whatsapp_signature_headers,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    deliveries_of_line,
)
from app.modules.channels.contracts import DeliveryStatus
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelSourceModel,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work"),
]

_SAID = "My card ending 4242 was charged twice"
_PHONE = "15550555577"
#: The profile name `_whatsapp_payload` sends.
_NAME = "Surface Test User"


async def _lines(conversation_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            (
                await session.scalars(
                    select(ChannelMessageModel)
                    .where(ChannelMessageModel.conversation_id == conversation_id)
                    .order_by(ChannelMessageModel.seq)
                )
            ).all()
        )


def _holds_nothing_said(ref: dict[str, object]) -> None:
    dumped = json.dumps(ref)
    assert _SAID not in dumped
    assert _NAME not in dumped
    assert ref["message_text"] == ""
    assert ref.get("sender_phone") is None
    assert ref.get("sender_email") is None
    assert ref.get("sender_display_name") is None
    assert "raw_payload" not in ref
    metadata = ref.get("metadata")
    assert isinstance(metadata, dict)
    assert "contacts" not in metadata and "attachments" not in metadata


async def test_a_heard_line_keeps_its_reply_target_and_nothing_said(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    await _create_agent_surface(
        authenticated_client, test_pod["id"], config={"type": "WHATSAPP"}
    )
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=_PHONE
    )
    payload = _whatsapp_payload(
        text=_SAID,
        message_id="wamid-e2e-reply-target",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone=_PHONE,
    )
    raw = json.dumps(payload).encode()
    response = await authenticated_client.post(
        "/surfaces/webhooks/whatsapp",
        content=raw,
        headers=build_whatsapp_signature_headers(raw_body=raw, app_secret="wa-secret"),
    )
    assert response.status_code == 200, response.text
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[script_text("Refunded the second charge.")],
    )

    lines = await _lines(context.conversation_id)
    [heard] = [line for line in lines if line.sender_kind != "AGENT"]
    assert heard.body == _SAID  # the line keeps the words, once
    assert heard.reply_ref is not None
    _holds_nothing_said(heard.reply_ref)
    target = heard.reply_ref["reply_target"]
    assert isinstance(target, dict)
    assert target["phone_number_id"] == "1234567890"

    async with async_session_maker() as session:
        place = await session.get(ChannelSourceModel, heard.source_id)
    assert place is not None and place.delivery_ref is not None
    _holds_nothing_said(place.delivery_ref)

    # And the answer still found its way back from the target alone.
    [answer] = [line for line in lines if line.agent_run_id is not None]
    [delivery] = await deliveries_of_line(answer.id)
    assert delivery.status is DeliveryStatus.SENT
