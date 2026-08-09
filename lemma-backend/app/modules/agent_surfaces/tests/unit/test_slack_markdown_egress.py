from __future__ import annotations

import pytest
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

pytestmark = pytest.mark.asyncio


def _event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="C1",
        external_thread_id="100.0",
        external_message_id="100.0",
        message_text="hi",
        reply_target={"channel": "C1", "thread_ts": "100.0"},
    )


def _capture(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def fake_post(self, **kwargs):
        sent.append(kwargs)
        return {"ok": True, "ts": "200.5", "channel": "C1"}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", fake_post)
    return sent


async def test_reply_is_delivered_as_a_markdown_block(monkeypatch):
    """Model-authored Markdown goes to Slack verbatim, in a markdown block.

    The whole point: a table or a heading now renders, where legacy mrkdwn in
    the ``text`` field would have printed the pipes and hashes literally.
    """
    sent = _capture(monkeypatch)
    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    body = "## Results\n\n| Region | Revenue |\n| --- | --- |\n| EMEA | 12 |"
    await svc.send_message(event=_event(), message=body)

    assert len(sent) == 1
    assert sent[0]["blocks"] == [{"type": "markdown", "text": body}]
    assert sent[0]["thread_ts"] == "100.0"


async def test_fallback_text_is_a_preview_not_the_whole_body(monkeypatch):
    """``text`` drives the push notification; it must not repeat the body."""
    sent = _capture(monkeypatch)
    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    body = "line one\n\n" + ("x" * 2000)
    await svc.send_message(event=_event(), message=body)

    assert len(sent[0]["text"]) < len(body)
    assert sent[0]["text"].endswith("…")
    # Newlines collapse so the preview stays one readable line.
    assert "\n" not in sent[0]["text"]


async def test_long_answer_is_split_across_messages(monkeypatch):
    """Slack caps markdown blocks at 12k *per payload*, so chunk into messages."""
    sent = _capture(monkeypatch)
    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    paragraph = "word " * 400  # ~2000 chars
    body = "\n\n".join([paragraph] * 12)  # comfortably over the cap
    await svc.send_message(event=_event(), message=body)

    assert len(sent) > 1
    for payload in sent:
        blocks = payload["blocks"]
        assert blocks[0]["type"] == "markdown"
        assert len(blocks[0]["text"]) <= MARKDOWN_BLOCK_CHAR_LIMIT
        assert payload["thread_ts"] == "100.0"


async def test_feedback_buttons_only_on_the_last_message(monkeypatch):
    """Feedback rates the answer, so a chunked answer gets exactly one rating."""
    sent = _capture(monkeypatch)
    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    paragraph = "word " * 400
    body = "\n\n".join([paragraph] * 12)
    await svc.send_message(
        event=_event(),
        message=body,
        metadata={"feedback_callback_id": "run-abc"},
    )

    assert len(sent) > 1
    for payload in sent[:-1]:
        assert [b["type"] for b in payload["blocks"]] == ["markdown"]
    last_blocks = sent[-1]["blocks"]
    assert [b["type"] for b in last_blocks] == ["markdown", "context_actions"]
    element = last_blocks[1]["elements"][0]
    assert element["type"] == "feedback_buttons"
    assert element["action_id"].endswith("run-abc")


async def test_no_feedback_buttons_without_a_callback_id(monkeypatch):
    """Question/approval fallbacks reuse send_message and must not be rated."""
    sent = _capture(monkeypatch)
    svc = SlackPlatformService(credentials={"access_token": "xoxb-test"})

    await svc.send_message(event=_event(), message="Approve this?")

    assert [b["type"] for b in sent[0]["blocks"]] == ["markdown"]
