from __future__ import annotations

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.slack.streaming import SlackStreamSurface

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


def _stream_fakes(monkeypatch) -> dict[str, list[dict]]:
    """Capture the four streaming calls; returns the recorded kwargs per call."""
    calls: dict[str, list[dict]] = {
        "start": [],
        "append": [],
        "stop": [],
        "post": [],
    }

    async def fake_start(self, **kwargs):
        calls["start"].append(kwargs)
        return {"ok": True, "ts": "200.5", "channel": "C1"}

    async def fake_append(self, **kwargs):
        calls["append"].append(kwargs)
        return {"ok": True}

    async def fake_stop(self, **kwargs):
        calls["stop"].append(kwargs)
        return {"ok": True}

    async def fake_post(self, **kwargs):
        calls["post"].append(kwargs)
        return {"ok": True, "ts": "300.1", "channel": "C1"}

    monkeypatch.setattr(AsyncWebClient, "chat_startStream", fake_start)
    monkeypatch.setattr(AsyncWebClient, "chat_appendStream", fake_append)
    monkeypatch.setattr(AsyncWebClient, "chat_stopStream", fake_stop)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_post)
    return calls


async def test_progress_opens_a_stream_and_appends_each_step(monkeypatch):
    """Steps are additive task chunks, not a message edited in place.

    Each new step completes the one in flight and opens the next, so Slack
    renders a timeline of what the agent did rather than a single line that
    overwrites its own history.
    """
    calls = _stream_fakes(monkeypatch)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    event = _event()

    handle = await stream.stream_progress(event, "Searching the web")
    assert handle["ts"] == "200.5"
    assert handle["stream"] is True
    assert handle["task_seq"] == 1
    assert calls["start"][0]["thread_ts"] == "100.0"
    assert calls["start"][0]["task_display_mode"] == "timeline"
    assert calls["append"][0]["chunks"] == [
        {
            "type": "task_update",
            "id": "step-1",
            "title": "Searching the web",
            "status": "in_progress",
        }
    ]

    handle2 = await stream.stream_progress(event, "Reading results", handle)
    assert handle2["task_seq"] == 2
    # The step in flight is completed, and the next opened, in one append.
    assert calls["append"][1]["chunks"] == [
        {
            "type": "task_update",
            "id": "step-1",
            "title": "Searching the web",
            "status": "complete",
        },
        {
            "type": "task_update",
            "id": "step-2",
            "title": "Reading results",
            "status": "in_progress",
        },
    ]
    # Nothing is posted or deleted along the way — it is all one message.
    assert calls["post"] == []


async def test_finish_progress_closes_the_stream_with_the_answer(monkeypatch):
    """The steps and the answer they produced end up as one message.

    The answer is *appended* and the stream then closed. Slack rejects a
    stopStream that tries to introduce the body itself — verified against a
    live workspace, where passing markdown_text to stopStream failed and the
    reply fell back to a separate message beside a dead "Thinking…" bubble.
    """
    calls = _stream_fakes(monkeypatch)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    event = _event()

    handle = await stream.stream_progress(event, "Searching the web")
    delivered = await stream.finish_progress(event, handle, "## Answer\n\nAll done.")

    assert delivered is True
    # The body rides append, not stop.
    answer_append = calls["append"][-1]
    assert answer_append["ts"] == "200.5"
    # Everything on a chunk stream is a chunk — mixing in top-level
    # markdown_text is what Slack rejects as streaming_mode_mismatch.
    assert "markdown_text" not in answer_append
    assert answer_append["chunks"] == [
        {
            "type": "task_update",
            "id": "step-1",
            "title": "Searching the web",
            "status": "complete",
        },
        {"type": "markdown_text", "text": "## Answer\n\nAll done."},
    ]
    # stop only finalises — it carries no text of its own.
    stop = calls["stop"][0]
    assert stop["ts"] == "200.5"
    assert "markdown_text" not in stop
    # The answer rode the stream, so no separate message was posted.
    assert calls["post"] == []


async def test_finish_progress_declines_without_a_live_stream(monkeypatch):
    """No stream means the caller must deliver the answer the ordinary way."""
    calls = _stream_fakes(monkeypatch)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})

    assert await stream.finish_progress(_event(), None, "answer") is False
    assert await stream.finish_progress(_event(), {"ts": "1"}, "   ") is False
    assert calls["stop"] == []


async def test_finish_progress_spills_an_oversized_answer_into_messages(monkeypatch):
    """An answer past the 12k markdown budget still arrives in full."""
    calls = _stream_fakes(monkeypatch)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    event = _event()

    handle = await stream.stream_progress(event, "Working")
    body = "\n\n".join(["word " * 400] * 12)
    assert await stream.finish_progress(event, handle, body) is True

    assert len(calls["stop"]) == 1
    # The overflow continues as ordinary markdown messages rather than vanishing.
    assert calls["post"]
    for payload in calls["post"]:
        assert payload["blocks"][0]["type"] == "markdown"


async def test_end_progress_disposes_of_a_stream_with_no_answer(monkeypatch):
    """A stream that will not carry the answer must not survive beside it.

    Leaving it strands a "Thinking…" bubble next to the real reply — two
    messages where the user should see one. Slack needs the stream stopped
    before it will accept the delete, so both calls fire in that order.
    """
    calls = _stream_fakes(monkeypatch)
    deletes: list[dict] = []

    async def fake_delete(self, **kwargs):
        deletes.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_delete", fake_delete)

    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    event = _event()
    handle = await stream.stream_progress(event, "Searching the web")
    await stream.end_progress(event, handle)

    assert calls["stop"][0]["ts"] == "200.5"
    assert calls["stop"][0]["chunks"][0]["status"] == "complete"
    assert deletes == [{"channel": "C1", "ts": "200.5"}]


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


async def test_streamed_text_is_a_chunk_not_top_level_markdown(monkeypatch):
    """Regression for a live `streaming_mode_mismatch`.

    A Slack stream is chunk-based or plain-text for its whole life. The step
    timeline makes it chunk-based, so model text has to arrive as a
    markdown_text chunk — sending top-level markdown_text on the same stream
    was rejected on every single append against a real workspace.
    """
    calls = _stream_fakes(monkeypatch)
    stream = SlackStreamSurface(credentials={"access_token": "xoxb-test"})
    event = _event()

    handle = await stream.append_stream_text(event, None, "Hello ")
    handle = await stream.append_stream_text(event, handle, "world")

    # Opened in the same mode the step timeline uses.
    assert calls["start"][0]["task_display_mode"] == "timeline"
    for append in calls["append"]:
        assert "markdown_text" not in append
    assert [a["chunks"] for a in calls["append"]] == [
        [{"type": "markdown_text", "text": "Hello "}],
        [{"type": "markdown_text", "text": "world"}],
    ]
    assert handle["streamed_text"] is True
