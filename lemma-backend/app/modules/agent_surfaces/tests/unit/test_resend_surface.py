from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.api.controllers.webhook_controller import (
    _normalize_resend_inbound,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfaceEventMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.resend.parser import ResendInboundParser
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.services.surface_service import AgentSurfaceService


def test_resend_is_email_and_default_webhook_binding():
    assert SurfacePlatform.RESEND.is_email is True
    # RESEND is system-credentialed email over a native webhook (no account_id).
    surface = AgentSurfaceEntity.create(
        pod_id=uuid4(),
        surface_type=SurfacePlatform.RESEND,
        agent_id=None,
        config=SurfaceConfig(),
        credential_mode=SurfaceCredentialMode.SYSTEM,
        account_id=None,
    )
    assert surface.mode is SurfaceMode.EMAIL
    assert surface.event_mode is SurfaceEventMode.WEBHOOK


def test_resend_inbound_parser_threads_and_builds_reply_target():
    parser = ResendInboundParser()
    event = parser.parse(
        {
            "from": "alice@example.com",
            "to": "pod-abc@ops.lemma.work",
            "subject": "Re: Question",
            "text": "Here is my answer",
            "message_id": "<m2@example.com>",
            "in_reply_to": "<m1@example.com>",
            "references": ["<root@example.com>", "<m1@example.com>"],
        }
    )
    assert event is not None
    assert event.platform is SurfacePlatform.RESEND
    assert event.sender_email == "alice@example.com"
    # Thread groups by the references root.
    assert event.external_thread_id == "<root@example.com>"
    assert event.reply_target["recipient_email"] == "alice@example.com"
    # Outbound references chain = inbound references + this message id.
    assert event.reply_target["references"][-1] == "<m2@example.com>"
    assert event.metadata["surface_address"] == "pod-abc@ops.lemma.work"


@pytest.mark.asyncio
async def test_resend_send_email_builds_resend_api_payload():
    service = ResendPlatformService(
        {"api_key": "re_test", "from_address": "pod-1@ops.lemma.work", "from_name": "Lemma"}
    )
    captured = {}

    async def _fake_post(self, url, json, headers):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _Resp:
            content = b"{}"

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "email-1"}

        return _Resp()

    with patch("httpx.AsyncClient.post", new=_fake_post):
        result = await service._send_email(
            recipient_email="bob@example.com",
            subject="Hello",
            in_reply_to="<m1@example.com>",
            references=["<m1@example.com>"],
            content="**hi** there",
            content_type="markdown",
            attachments=[("note.txt", b"data", "text/plain")],
        )

    assert result == {"id": "email-1"}
    assert captured["url"].endswith("/emails")
    assert captured["headers"]["Authorization"] == "Bearer re_test"
    body = captured["json"]
    assert body["from"] == "Lemma <pod-1@ops.lemma.work>"
    assert body["to"] == ["bob@example.com"]
    assert "<strong>hi</strong>" in body["html"]
    assert body["headers"]["In-Reply-To"] == "<m1@example.com>"
    assert body["attachments"][0]["filename"] == "note.txt"


def test_provision_resend_address_is_unique_per_pod():
    pod_id = uuid4()
    address = AgentSurfaceService._provision_resend_address(pod_id)
    assert address.endswith("@ops.lemma.work")
    assert pod_id.hex[:12] in address
    # Different pods get different addresses.
    assert address != AgentSurfaceService._provision_resend_address(uuid4())


def test_normalize_resend_inbound_handles_envelope_and_shapes():
    normalized = _normalize_resend_inbound(
        {
            "type": "email.received",
            "data": {
                "from": {"address": "alice@example.com", "name": "Alice"},
                "to": [{"address": "pod-1@ops.lemma.work"}],
                "subject": "Hi",
                "text": "body",
                "headers": [
                    {"name": "Message-ID", "value": "<m9@example.com>"},
                    {"name": "References", "value": "<r1@example.com> <r2@example.com>"},
                ],
            },
        }
    )
    assert normalized["from"] == "alice@example.com"
    assert normalized["from_name"] == "Alice"
    assert normalized["to"] == "pod-1@ops.lemma.work"
    assert normalized["message_id"] == "<m9@example.com>"
    assert normalized["references"] == ["<r1@example.com>", "<r2@example.com>"]
