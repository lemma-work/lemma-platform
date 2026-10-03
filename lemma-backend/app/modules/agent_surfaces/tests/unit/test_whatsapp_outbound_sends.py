"""What Lemma puts on a WhatsApp phone, and what it does when Meta says no.

Each test is a shape Meta refused or a person could not read: button titles
two characters over the limit, two buttons with the same stub, an approval card
cut through the command being approved, a group reply nobody could attribute.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.domain.models import (
    APPROVAL_DECISION_APPROVE,
    APPROVAL_DECISION_DENY,
    SurfaceApprovalButton,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionOption,
)
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy
from app.modules.agent_surfaces.platforms.sent_message_ids import (
    collect_sent_message_ids,
    record_sent_message_id,
)
from app.modules.agent_surfaces.platforms.whatsapp import media as whatsapp_media
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    TYPE_YOUR_OWN_FOOTER,
    build_whatsapp_interactive,
    truncate_whatsapp_text,
    whatsapp_cta_url_payload,
    whatsapp_upload_mime,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)


def _question(*labels: str) -> SurfaceQuestion:
    return SurfaceQuestion(
        header="pick",
        question="Which one?",
        options=[SurfaceQuestionOption(label=label) for label in labels],
    )


# --- payload shapes -----------------------------------------------------------


@pytest.mark.parametrize("limit", [4, 20, 24, 72, 1024])
def test_a_cut_string_fits_the_limit_it_was_cut_for(limit):
    """It kept ``limit - 1`` characters and added three dots: two over."""
    cut = truncate_whatsapp_text("x" * 2000, limit)
    assert len(cut) == limit
    assert cut.endswith("...")


def test_a_cta_label_fits_meta_s_twenty_characters():
    plan = SurfaceDisplayRenderPlan(
        title="Report",
        resource_type="FILE",
        actions=[
            SurfaceDisplayAction(
                label="Open the quarterly revenue report", url="https://x"
            )
        ],
    )
    payload = whatsapp_cta_url_payload(recipient_wa_id="1555", render_plan=plan)
    label = payload["interactive"]["action"]["parameters"]["display_text"]
    assert len(label) <= 20


def test_short_distinct_options_are_buttons_with_the_type_your_own_footer():
    interactive = build_whatsapp_interactive("conv|tool", _question("Yes", "No"))

    assert interactive["type"] == "button"
    assert interactive["footer"] == {"text": TYPE_YOUR_OWN_FOOTER}
    titles = [b["reply"]["title"] for b in interactive["action"]["buttons"]]
    assert titles == ["Yes", "No"]


def test_a_label_too_long_for_a_button_becomes_a_list_row():
    """Cut at 20 characters, two options read the same; a list row has room."""
    interactive = build_whatsapp_interactive(
        "conv|tool",
        _question("Ship it to the office", "Ship it to the warehouse"),
    )

    assert interactive["type"] == "list"
    assert interactive["footer"] == {"text": TYPE_YOUR_OWN_FOOTER}
    rows = interactive["action"]["sections"][0]["rows"]
    assert [row["title"] for row in rows] == [
        "Ship it to the office",
        "Ship it to the warehouse",
    ]


def test_list_titles_that_cutting_makes_identical_are_numbered():
    stem = "Send the signed contract to "
    interactive = build_whatsapp_interactive(
        "conv|tool", _question(stem + "legal", stem + "finance")
    )

    rows = interactive["action"]["sections"][0]["rows"]
    titles = [row["title"] for row in rows]
    assert len(set(titles)) == 2
    assert titles[0].startswith("1. ") and titles[1].startswith("2. ")
    assert all(len(title) <= 24 for title in titles)
    # The whole label survives on the line beneath.
    assert rows[1]["description"] == stem + "finance"
    # The id still carries the full label, so the answer is what was meant.
    assert rows[1]["id"].endswith("~" + stem + "finance")


def test_text_like_files_upload_as_plain_text_under_their_own_name():
    """``text/markdown`` is not on Meta's document list; text is still text."""
    for name, mime in (
        ("report.md", "text/markdown"),
        ("rows.csv", "text/csv"),
        ("data.json", "application/json"),
        ("chart.svg", "image/svg+xml"),
        ("notes.md", "application/octet-stream"),
    ):
        assert (
            whatsapp_upload_mime(file_name=name, mime_type=mime, kind="document")
            == "text/plain"
        )
    assert (
        whatsapp_upload_mime(
            file_name="a.pdf", mime_type="application/pdf", kind="document"
        )
        == "application/pdf"
    )
    assert (
        whatsapp_upload_mime(file_name="a.png", mime_type="image/png", kind="image")
        == "image/png"
    )


# --- the service ---------------------------------------------------------------


class _Recorder(WhatsAppClient):
    """The real client with the HTTP call replaced by a script."""

    def __init__(self, outcomes: list[object] | None = None) -> None:
        super().__init__(
            access_token="t",
            api_base="http://x/v21.0",
            retry_policy=RetryPolicy(max_attempts=1, base_delay=0, max_delay=0),
        )
        self.outcomes = list(outcomes or [])
        self.posts: list[dict] = []
        self.uploads: list[dict] = []

    async def _post_json(self, url, *, json, method):
        self.posts.append(json)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or {"messages": [{"id": f"wamid.out-{len(self.posts)}"}]}

    async def upload_media(self, *, phone_number_id, file_name, file_bytes, mime_type):
        self.uploads.append({"file_name": file_name, "mime_type": mime_type})
        return "media-1"


def _event(*, group: bool = False) -> ParsedInboundSurfaceEvent:
    target = (
        {"phone_number_id": "phone-1", "group_id": "group-1"}
        if group
        else {"phone_number_id": "phone-1", "sender_wa_id": "15551234567"}
    )
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=(
            ConversationType.EXTERNAL_GROUP if group else ConversationType.EXTERNAL_DM
        ),
        external_thread_id="group-1" if group else "15551234567@phone-1",
        external_message_id="wamid.in",
        sender_phone="15551234567",
        message_text="hi",
        is_dm=not group,
        reply_target=target,
    )


def _service(client: _Recorder) -> WhatsAppPlatformService:
    return WhatsAppPlatformService(
        {"access_token": "t", "phone_number_id": "phone-1"}, client=client
    )


async def test_a_group_reply_quotes_the_message_it_answers_on_its_first_part():
    client = _Recorder()

    await _service(client).send_message(_event(group=True), ("word " * 900).strip())

    assert len(client.posts) == 2
    assert client.posts[0]["context"] == {"message_id": "wamid.in"}
    assert "context" not in client.posts[1]


async def test_a_refused_quote_is_dropped_and_the_reply_still_sent():
    """Meta documents ``context`` for one-to-one chats only."""
    client = _Recorder([WhatsAppApiError(method="messages", status_code=400)])

    await _service(client).send_message(_event(group=True), "the answer")

    assert [("context" in post) for post in client.posts] == [True, False]
    assert client.posts[1]["text"]["body"] == "the answer"


async def test_a_direct_reply_quotes_nothing():
    client = _Recorder()

    await _service(client).send_message(_event(), "the answer")

    assert "context" not in client.posts[0]


async def test_a_message_formatting_empties_is_an_error_not_a_delivery():
    client = _Recorder()

    with pytest.raises(AgentSurfacePlatformError):
        await _service(client).send_message(_event(), "---")

    assert client.posts == []


async def test_sent_message_ids_are_collected_for_whoever_asks():
    client = _Recorder()

    with collect_sent_message_ids() as ids:
        await _service(client).send_message(_event(), "one")
        await _service(client).send_message(_event(), "two")

    assert ids == ["wamid.out-1", "wamid.out-2"]
    # Outside a collector recording is a no-op, not an error.
    record_sent_message_id("wamid.nobody-asked")


async def test_a_typing_indicator_is_not_recorded_as_a_sent_message():
    client = _Recorder()

    with collect_sent_message_ids() as ids:
        await client.mark_read_and_typing(phone_number_id="phone-1", message_id="m")

    assert ids == []


async def test_an_approval_too_long_for_the_card_is_sent_in_full_first():
    command = "lemma records delete orders --id 42 " + "--confirm " * 150
    plan = SurfaceApprovalRenderPlan(
        title="Delete order 42",
        reason="Cleaning up. " * 40,
        action_summary=command,
        callback_id="conv|tool",
        buttons=[
            SurfaceApprovalButton(label="Approve", decision=APPROVAL_DECISION_APPROVE),
            SurfaceApprovalButton(label="Deny", decision=APPROVAL_DECISION_DENY),
        ],
    )
    client = _Recorder()

    assert await _service(client)._render_decision(_event(), plan) is True

    details, card = client.posts[0], client.posts[-1]
    full_text = "".join(
        post["text"]["body"] for post in client.posts if post["type"] == "text"
    )
    assert details["type"] == "text"
    assert command.split()[-1] in full_text and "Cleaning up." in full_text
    assert card["type"] == "interactive"
    assert len(card["interactive"]["body"]["text"]) <= 1024
    assert "message above" in card["interactive"]["body"]["text"]


async def test_a_short_approval_is_one_card():
    plan = SurfaceApprovalRenderPlan(
        title="Delete order 42",
        callback_id="conv|tool",
        buttons=[
            SurfaceApprovalButton(label="Approve", decision=APPROVAL_DECISION_APPROVE)
        ],
    )
    client = _Recorder()

    await _service(client)._render_decision(_event(), plan)

    assert [post["type"] for post in client.posts] == ["interactive"]


async def test_a_voice_note_is_flagged_and_its_caption_follows_as_text():
    client = _Recorder()

    sent = await _service(client).send_voice_note(
        _event(),
        file_name="say.ogg",
        audio_bytes=b"OggS",
        mime_type="audio/ogg; codecs=opus",
        caption="Here is the summary",
    )

    assert sent is True
    assert client.posts[0]["audio"] == {"id": "media-1", "voice": True}
    assert client.posts[1]["text"]["body"] == "Here is the summary"


async def test_a_refused_voice_flag_is_retried_as_plain_audio():
    client = _Recorder([WhatsAppApiError(method="messages", status_code=400)])

    sent = await _service(client).send_voice_note(
        _event(), file_name="say.ogg", audio_bytes=b"OggS", mime_type="audio/ogg"
    )

    assert sent is True
    assert [post["audio"] for post in client.posts] == [
        {"id": "media-1", "voice": True},
        {"id": "media-1"},
    ]


async def test_audio_that_is_not_ogg_is_left_to_the_file_rung():
    client = _Recorder()

    sent = await _service(client).send_voice_note(
        _event(), file_name="say.mp3", audio_bytes=b"ID3", mime_type="audio/mpeg"
    )

    assert sent is False
    assert client.posts == [] and client.uploads == []


async def test_a_large_photo_is_uploaded_and_sent_as_a_document():
    client = _Recorder()

    sent = await whatsapp_media.send_file(
        client,
        phone_number_id="phone-1",
        recipient_wa_id="15551234567",
        file_name="photo.png",
        file_bytes=b"x" * (6 * 1024 * 1024),
        mime_type="image/png",
    )

    assert sent is True
    assert [post["type"] for post in client.posts] == ["document"]


async def test_an_upload_that_never_reaches_meta_falls_back_to_a_link():
    """A transport error escaped as an exception the caller did not expect."""

    class _Unreachable(_Recorder):
        async def upload_media(self, **_kwargs):
            raise httpx.ConnectError("no route")

    sent = await whatsapp_media.send_file(
        _Unreachable(),
        phone_number_id="phone-1",
        recipient_wa_id="15551234567",
        file_name="a.pdf",
        file_bytes=b"%PDF",
        mime_type="application/pdf",
    )

    assert sent is False
