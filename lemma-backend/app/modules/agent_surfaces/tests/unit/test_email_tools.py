from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.platforms.email_render import render_email_content


class _FakeHttpResponse:
    def __init__(self, *, json_data=None) -> None:
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def _email_ctx(platform: str) -> SimpleNamespace:
    """A run context on an email surface with pause support available (so only
    the email guard, not the pause-signal guard, can trigger the fallback)."""
    return SimpleNamespace(
        deps=SimpleNamespace(
            agent_run_id=uuid4(),
            conversation_id=uuid4(),
            supports_pause_signal=True,
            surface_platform=platform,
        ),
        tool_call_id="tool-1",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["GMAIL", "OUTLOOK", "RESEND"])
async def test_ask_user_fails_fast_on_email_surface(platform):
    """ask_user must never pause (raise AgentInputRequired) on an email surface —
    it returns a recoverable interaction_fallback instead so the run completes."""
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.models import AskUserRequest
    from app.modules.agent.tools.user_interaction.pydantic_adapter import ask_user

    request = AskUserRequest.model_validate(
        {
            "questions": [
                {
                    "header": "color",
                    "question": "Which color?",
                    "options": [{"label": "Red"}, {"label": "Blue"}],
                }
            ]
        }
    )
    try:
        response = await ask_user(_email_ctx(platform), request)
    except AgentInputRequired:  # pragma: no cover - the bug this guards against
        pytest.fail("ask_user paused the run on an email surface")
    assert response.success is False
    assert response.interaction_fallback is True


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["GMAIL", "OUTLOOK", "RESEND"])
async def test_request_approval_fails_fast_on_email_surface(platform):
    """request_approval must never pause on an email surface."""
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.pydantic_adapter import (
        request_approval,
    )

    try:
        response = await request_approval(
            _email_ctx(platform),
            tool_name="pod_write_record",
            args={"table_id": "t", "data": {}},
            title="Write a record",
        )
    except AgentInputRequired:  # pragma: no cover - the bug this guards against
        pytest.fail("request_approval paused the run on an email surface")
    assert response.success is False
    assert response.interaction_fallback is True


@pytest.mark.asyncio
async def test_ask_user_still_pauses_on_chat_surface():
    """The email guard must not affect chat surfaces — ask_user still pauses."""
    from app.modules.agent.tools.tool_errors import AgentInputRequired
    from app.modules.agent.tools.user_interaction.models import AskUserRequest
    from app.modules.agent.tools.user_interaction.pydantic_adapter import ask_user

    request = AskUserRequest.model_validate(
        {
            "questions": [
                {
                    "header": "color",
                    "question": "Which color?",
                    "options": [{"label": "Red"}, {"label": "Blue"}],
                }
            ]
        }
    )
    with pytest.raises(AgentInputRequired):
        await ask_user(_email_ctx("WHATSAPP"), request)


def test_render_email_content_adds_display_resource_html_card():
    plain, html = render_email_content(
        content="I prepared the report.",
        content_type="text",
        display_resource_plans=[
            SurfaceDisplayRenderPlan(
                resource_type="FILE",
                title="report.pdf",
                summary="PDF · 2.3 MB",
                actions=[
                    SurfaceDisplayAction(
                        label="Open file",
                        url="https://app.example.test/pod/p/files?file=/me/report.pdf",
                    )
                ],
            )
        ],
    )

    assert "I prepared the report." in plain
    assert "report.pdf" in plain
    assert "PDF · 2.3 MB" in plain
    assert html is not None
    assert "Open file" in html
    assert "https://app.example.test" in html


def _email_event(platform: str, **reply_target):
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )

    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id=str(reply_target.get("thread_id") or "thread-1"),
        message_text="Need review",
        reply_target=dict(reply_target),
    )


@pytest.mark.asyncio
async def test_a_gmail_envelope_is_sent_as_one_message_with_its_attachment(monkeypatch):
    """The reply tool is gone; the adapter folds the envelope into one send."""
    from app.modules.agent_surfaces.domain.envelope import (
        EnvelopeFile,
        SurfaceEnvelope,
    )
    from app.modules.agent_surfaces.platforms.gmail.adapter import (
        ComposioGmailSurfaceAdapter,
    )

    sent: list[dict] = []

    async def fake_post(self, url: str, **kwargs):
        assert url.endswith("/gmail/v1/users/me/messages/send")
        sent.append(kwargs["json"])
        return _FakeHttpResponse(json_data={"id": "gmail-sent-1"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    receipt = await ComposioGmailSurfaceAdapter().deliver(
        credentials={
            "access_token": "gmail-token",
            "api_base_url": "https://gmail.example.test",
        },
        event=_email_event(
            "GMAIL",
            recipient_email="rahul@example.com",
            subject="Re: Need review",
            thread_id="gmail-thread-1",
            in_reply_to="<gmail-message-1@example.com>",
            references=["<gmail-message-1@example.com>"],
        ),
        envelope=SurfaceEnvelope(
            text="## Done\nPlease see the attached report.",
            files=[
                EnvelopeFile(
                    file_name="report.txt",
                    content=b"hello world",
                    mime_type="text/plain",
                )
            ],
        ),
    )

    assert len(sent) == 1, "one envelope is one email, attachment included"
    assert sent[0]["threadId"] == "gmail-thread-1"
    assert receipt.delivered


@pytest.mark.asyncio
async def test_an_outlook_envelope_with_a_file_goes_through_a_draft(monkeypatch):
    """Graph refuses to attach bytes to a direct reply, so the send path drafts.

    send_message used to reach past the method that knew this, which is how the
    path carrying files and the path carrying the answer came to differ.
    """
    from app.modules.agent_surfaces.domain.envelope import (
        EnvelopeFile,
        SurfaceEnvelope,
    )
    from app.modules.agent_surfaces.platforms.outlook.adapter import (
        ComposioOutlookSurfaceAdapter,
    )

    calls: list[str] = []

    async def fake_post(self, url: str, **kwargs):
        calls.append(url)
        if url.endswith("/createReply"):
            return _FakeHttpResponse(json_data={"id": "draft-1"})
        if url.endswith("/attachments"):
            assert kwargs["json"]["name"] == "brief.txt"
            return _FakeHttpResponse(json_data={"id": "attachment-1"})
        return _FakeHttpResponse()

    async def fake_patch(self, url: str, **kwargs):
        calls.append(url)
        return _FakeHttpResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    await ComposioOutlookSurfaceAdapter().deliver(
        credentials={
            "access_token": "outlook-token",
            "api_base_url": "https://graph.example.test",
        },
        event=_email_event(
            "OUTLOOK",
            recipient_email="rahul@example.com",
            subject="Re: Need review",
            message_id="graph-message-1",
        ),
        envelope=SurfaceEnvelope(
            text="Done. See attachment.",
            files=[
                EnvelopeFile(
                    file_name="brief.txt",
                    content=b"brief body",
                    mime_type="text/plain",
                )
            ],
        ),
    )

    assert any(url.endswith("/createReply") for url in calls)
    assert any(url.endswith("/attachments") for url in calls)
    assert any(url.endswith("/send") for url in calls)
