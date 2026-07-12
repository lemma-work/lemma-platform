from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    GmailSurfaceEventMetadata,
    OutlookSurfaceEventMetadata,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.platforms.email_common import render_email_content
from app.modules.agent_surfaces.platforms.gmail.tools import build_gmail_surface_toolset
from app.modules.agent_surfaces.platforms.outlook.tools import (
    build_outlook_surface_toolset,
)
from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


class _FakeHttpResponse:
    def __init__(self, *, json_data=None) -> None:
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def _email_ctx(platform: str) -> SimpleNamespace:
    """A run context on an email surface with pause support available (so only
    the email guard, not the daemon guard, can trigger the fallback)."""
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
                title="File: /me/report.pdf",
                summary="A file is ready to inspect.",
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
    assert "File: /me/report.pdf" in plain
    assert html is not None
    assert "Open file" in html
    assert "https://app.example.test" in html


@pytest.mark.asyncio
async def test_gmail_reply_email_sends_html_and_attachment(monkeypatch):
    toolset = build_gmail_surface_toolset(
        credentials={
            "access_token": "gmail-token",
            "api_base_url": "https://gmail.example.test",
        }
    )
    tool = toolset.tools["gmail_reply_email"]

    async def fake_read_file(self, path: str):
        assert path == "notes/report.txt"
        return "hello world"

    async def fake_post(self, url: str, **kwargs):
        assert url.endswith("/gmail/v1/users/me/messages/send")
        payload = kwargs["json"]
        assert payload["threadId"] == "gmail-thread-1"
        return _FakeHttpResponse(json_data={"id": "gmail-sent-1"})

    monkeypatch.setattr(WorkspaceFileManager, "read_file", fake_read_file)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="GMAIL",
            external_channel_id="assistant@gmail.test",
            external_thread_id="gmail-thread-1",
            surface_metadata=GmailSurfaceEventMetadata(
                mailbox_email="assistant@gmail.test",
                subject="Need review",
                thread_id="gmail-thread-1",
                message_id="gmail-message-1",
                reply_to_email="rahul@example.com",
                references=["<gmail-message-1@example.com>"],
                in_reply_to="<gmail-message-1@example.com>",
            ),
        )
    )
    request = SimpleNamespace(
        content="## Done\nPlease see the attached report.",
        content_type="markdown",
        attachment_paths=["notes/report.txt"],
        subject=None,
    )

    response = await tool.function(ctx, request)

    assert response.success is True
    assert response.message_id == "gmail-sent-1"
    assert response.attachment_count == 1


@pytest.mark.asyncio
async def test_outlook_reply_email_sends_graph_file_attachments(monkeypatch):
    toolset = build_outlook_surface_toolset(
        credentials={
            "access_token": "outlook-token",
            "api_base_url": "https://graph.example.test",
        }
    )
    tool = toolset.tools["outlook_reply_email"]

    async def fake_read_file(self, path: str):
        assert path == "docs/brief.txt"
        return "brief body"

    calls: list[tuple[str, dict | None]] = []

    async def fake_post(self, url: str, **kwargs):
        calls.append((url, kwargs.get("json")))
        if url.endswith("/v1.0/me/messages/graph-message-1/createReply"):
            return _FakeHttpResponse(json_data={"id": "draft-1"})
        if url.endswith("/v1.0/me/messages/draft-1/attachments"):
            payload = kwargs["json"]
            assert payload["@odata.type"] == "#microsoft.graph.fileAttachment"
            assert payload["name"] == "brief.txt"
            return _FakeHttpResponse(json_data={"id": "attachment-1"})
        if url.endswith("/v1.0/me/messages/draft-1/send"):
            return _FakeHttpResponse()
        raise AssertionError(f"Unexpected POST url: {url}")

    async def fake_patch(self, url: str, **kwargs):
        assert url.endswith("/v1.0/me/messages/draft-1")
        payload = kwargs["json"]
        assert payload["subject"] == "Re: Need review"
        assert payload["body"]["contentType"] == "HTML"
        assert "Done. See attachment." in payload["body"]["content"]
        return _FakeHttpResponse()

    monkeypatch.setattr(WorkspaceFileManager, "read_file", fake_read_file)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="OUTLOOK",
            external_channel_id="assistant@outlook.test",
            external_thread_id="outlook-thread-1",
            surface_metadata=OutlookSurfaceEventMetadata(
                mailbox_email="assistant@outlook.test",
                subject="Need review",
                thread_id="outlook-thread-1",
                message_id="graph-message-1",
                internet_message_id="<outlook-message-1@example.com>",
                reply_to_email="rahul@example.com",
                references=["<outlook-message-1@example.com>"],
                in_reply_to="<outlook-message-1@example.com>",
            ),
        )
    )
    request = SimpleNamespace(
        content="<p>Done. See attachment.</p>",
        content_type="html",
        attachment_paths=["docs/brief.txt"],
        subject=None,
    )

    response = await tool.function(ctx, request)

    assert response.success is True
    assert response.attachment_count == 1
    assert [url for url, _ in calls] == [
        "https://graph.example.test/v1.0/me/messages/graph-message-1/createReply",
        "https://graph.example.test/v1.0/me/messages/draft-1/attachments",
        "https://graph.example.test/v1.0/me/messages/draft-1/send",
    ]
