from __future__ import annotations

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

pytestmark = pytest.mark.asyncio


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="C1",
        external_thread_id="100.0",
        external_message_id="100.0",
        message_text="hi",
        reply_target={"channel": "C1", "thread_ts": "100.0"},
    )


async def test_slack_stream_progress_posts_updates_then_deletes(monkeypatch):
    posts: list[dict] = []
    updates: list[dict] = []
    deletes: list[dict] = []

    async def fake_post(self, **kwargs):
        posts.append(kwargs)
        return {"ok": True, "ts": "200.5", "channel": "C1"}

    async def fake_update(self, **kwargs):
        updates.append(kwargs)
        return {"ok": True}

    async def fake_delete(self, **kwargs):
        deletes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_post)
    monkeypatch.setattr(AsyncWebClient, "chat_update", fake_update)
    monkeypatch.setattr(AsyncWebClient, "chat_delete", fake_delete)

    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    event = _event()

    # First call posts a placeholder and returns its handle.
    handle = await svc.stream_progress(event, "Searching the web")
    assert handle == {"ts": "200.5", "channel": "C1"}
    assert posts and "⏳" in posts[0]["text"]
    assert posts[0]["thread_ts"] == "100.0"

    # Subsequent calls edit the same message in place (chat.update).
    handle2 = await svc.stream_progress(event, "Reading results", handle)
    assert handle2 == handle
    assert updates and updates[0]["ts"] == "200.5"
    assert "Reading results" in updates[0]["text"]

    # end_progress deletes the placeholder.
    await svc.end_progress(event, handle)
    assert deletes and deletes[0]["ts"] == "200.5"


async def test_slack_send_file_bytes_retries_without_customized_identity(monkeypatch):
    completions: list[dict] = []
    uploads: list[dict] = []

    async def fake_upload_ticket(self, **kwargs):
        uploads.append(kwargs)
        return {"ok": True, "upload_url": "https://upload.example.test", "file_id": "F1"}

    async def fake_complete(self, **kwargs):
        completions.append(kwargs)
        if len(completions) == 1:
            raise SlackApiError(
                "custom identity rejected",
                {
                    "ok": False,
                    "error": "invalid_arguments",
                    "response_metadata": {"messages": ["username is not allowed"]},
                },
            )
        return {"ok": True}

    class FakeUploadResponse:
        def raise_for_status(self):
            return None

    class FakeHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, files):
            assert url == "https://upload.example.test"
            assert files["file"][0] == "report.txt"
            return FakeUploadResponse()

    monkeypatch.setattr(AsyncWebClient, "files_getUploadURLExternal", fake_upload_ticket)
    monkeypatch.setattr(AsyncWebClient, "files_completeUploadExternal", fake_complete)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.slack.service.httpx.AsyncClient",
        FakeHttpClient,
    )

    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})
    sent = await svc.send_file_bytes(
        _event(),
        file_name="report.txt",
        file_bytes=b"hello",
        mime_type="text/plain",
        caption="Report",
    )

    assert sent is True
    assert uploads == [{"filename": "report.txt", "length": 5}]
    assert len(completions) == 2
    assert completions[0]["initial_comment"] == "Report"
    assert completions[1]["files"] == [{"id": "F1", "title": "Report"}]
