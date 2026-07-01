from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_agent_surface,
    _set_user_mobile_number,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_whatsapp_signature_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e


async def test_whatsapp_built_in_dm_surface_handles_payload_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    agent, _surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP"},
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550555555",
    )

    payload = _whatsapp_payload(
        text="Hello from WhatsApp",
        message_id="wamid-e2e-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550555555",
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/whatsapp",
        content=raw_body,
        headers=build_whatsapp_signature_headers(
            raw_body=raw_body,
            app_secret="wa-secret",
        ),
    )
    assert response.status_code == 200, response.text

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[script_text("E2E agent reply [WHATSAPP]")],
    )
    assert isinstance(context, SurfaceChatContext)

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=agent["name"],
        external_thread_id="15550555555@1234567890",
    )
    assert conversation is not None
    assert conversation["metadata"]["surface_platform"] == "WHATSAPP"

    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=2)
    final_messages = [
        message
        for message in whatsapp_messages
        if message.get("type") == "text"
    ]
    assert final_messages
    assert "E2E agent reply [WHATSAPP]" in final_messages[-1]["text"]["body"]
