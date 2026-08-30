from __future__ import annotations


import pytest
from pydantic import BaseModel

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.common import (
    ProviderFailure,
    SurfaceFileAttachment,
    attachment_tool_hint,
    channel_author_label,
    coerce_attachments,
    platform_webhook_url,
    provider_failure,
    render_attachment_prompt_block,
    render_attachment_summary_suffix,
    select_attachment,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages

pytestmark = pytest.mark.e2e


def _slack_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="T-contract",
        external_channel_id="D-contract",
        external_thread_id="1700000000.000001",
        external_message_id="1700000000.000001",
        sender_external_user_id="U-contract",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=True,
        mentioned_agent=True,
        reply_target={"channel": "D-contract", "thread_ts": "1700000000.000001"},
    )


def _teams_event(fake_teams) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="tenant-contract",
        external_channel_id="19:channel",
        external_thread_id="activity-root",
        external_message_id="activity-reply",
        sender_external_user_id="29:user",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=False,
        mentioned_agent=True,
        reply_target={
            "service_url": fake_teams.service_url,
            "conversation_id": "conversation-contract",
            "reply_to_id": "activity-root",
        },
    )


def _telegram_event(*, is_dm: bool = True) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id="424242",
        external_thread_id="424242",
        external_message_id="111",
        sender_external_user_id="424242",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=is_dm,
        mentioned_agent=True,
        reply_target={"chat_id": "424242", "message_id": 111},
    )


def _whatsapp_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="waba-contract",
        external_channel_id="phone-contract",
        external_thread_id="15551234567@phone-contract",
        external_message_id="wamid.contract",
        sender_external_user_id="15551234567",
        sender_phone="15551234567",
        sender_display_name="Contract User",
        message_text="hello",
        is_dm=True,
        mentioned_agent=True,
        reply_target={
            "phone_number_id": "phone-contract",
            "sender_wa_id": "15551234567",
        },
    )


def _resend_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="resend-thread-1",
        external_message_id="resend-message-1",
        sender_external_user_id="sender@example.test",
        sender_email="sender@example.test",
        sender_display_name="Sender",
        message_text="hello",
        should_start_conversation=True,
        reply_target={
            "recipient_email": "sender@example.test",
            "subject": "Contract Subject",
            "in_reply_to": "<resend-message-1@example.test>",
            "references": ["<resend-root@example.test>"],
        },
    )


async def test_slack_final_answer_contract(fake_slack, message_store):
    service = SlackPlatformService(
        credentials={
            "access_token": "xoxb-contract",
            "scope": "chat:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
        }
    )

    await service.send_message(
        event=_slack_event(),
        message="*Contract* reply",
        metadata={"agent_display_name": "Contract Agent"},
    )

    messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/api/chat.postMessage"
    assert payload["_authorization"] == "Bearer xoxb-contract"
    assert payload["channel"] == "D-contract"
    assert payload["thread_ts"] == "1700000000.000001"
    assert payload["text"] == "*Contract* reply"
    assert payload["username"] == "Contract Agent"


async def test_teams_final_answer_contract(fake_teams, message_store, monkeypatch):
    adapter = TeamsSurfaceAdapter()

    async def _fake_bot_token(self, tenant_id=None):
        assert tenant_id == "tenant-contract"
        return "teams-contract-token"

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)

    await adapter.send_message(
        credentials={},
        event=_teams_event(fake_teams),
        message="**Contract** reply",
    )

    messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_authorization"] == "Bearer teams-contract-token"
    assert (
        payload["_path"] == "/teams/v3/conversations/conversation-contract/activities"
    )
    assert payload["body"] == {
        "type": "message",
        "text": "**Contract** reply",
        "textFormat": "markdown",
        "replyToId": "activity-root",
    }


async def test_telegram_final_answer_contract_and_retry(fake_telegram, message_store):
    service = TelegramPlatformService(
        {
            "bot_token": "telegram-contract-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        }
    )
    # `sendRichMessage`, not `sendMessage`: that is the method a send is
    # actually made with. This used to fail — and assert on — `sendMessage`,
    # which was only ever reached because the mock carried no rich route and
    # the fallback swallowed the 404. So the retry under test was the
    # fallback's, and the real path was never exercised at all.
    fake_telegram.fail_next["sendRichMessage"] = 1

    await service.send_message(
        event=_telegram_event(),
        message="Contract *reply*",
    )

    messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/bottelegram-contract-token/sendRichMessage"
    assert payload["chat_id"] == "424242"
    # A DM quotes nothing: the event an outbound is built from is the link's
    # last inbound, not the message being answered, so a quote here would tag
    # whatever arrived most recently.
    assert "reply_parameters" not in payload
    # The rich method carries the markdown as written; escaping is the
    # fallback's job, so there is no parse_mode on this one.
    assert payload["rich_message"]["markdown"] == "Contract *reply*"


async def test_telegram_falls_back_to_send_message_where_rich_is_unavailable(
    fake_telegram, message_store
):
    """The older contract, kept covered now that rich is the path taken.

    A bot API without `sendRichMessage` answers 404 and the reply must still
    arrive — escaped as MarkdownV2, through `sendMessage`. Until the mock
    carried a rich route this was the only path any Telegram e2e ever took, so
    it was covered by accident; now it has to be asked for.
    """
    service = TelegramPlatformService(
        {
            "bot_token": "telegram-contract-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        }
    )
    fake_telegram.unavailable.add("sendRichMessage")

    await service.send_message(
        event=_telegram_event(),
        message="Contract *reply*",
    )

    messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    payload = messages[-1]
    assert payload["_path"] == "/bottelegram-contract-token/sendMessage"
    assert payload["parse_mode"] == "MarkdownV2"
    assert "Contract" in payload["text"]


async def test_telegram_quotes_the_inbound_message_in_a_group(
    fake_telegram, message_store
):
    service = TelegramPlatformService(
        {
            "bot_token": "telegram-contract-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        }
    )

    await service.send_message(
        event=_telegram_event(is_dm=False),
        message="Contract reply",
    )

    messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    payload = messages[-1]
    assert payload["reply_parameters"] == {
        "message_id": 111,
        "allow_sending_without_reply": True,
    }


async def test_whatsapp_final_answer_contract(fake_whatsapp, message_store):
    service = WhatsAppPlatformService(
        {
            "access_token": "wa-contract-token",
            "phone_number_id": "phone-contract",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        }
    )

    await service.send_message(event=_whatsapp_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/v21.0/phone-contract/messages"
    assert payload["_authorization"] == "Bearer wa-contract-token"
    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "15551234567"
    assert payload["type"] == "text"
    assert payload["text"] == {"body": "Contract reply"}


async def test_chat_surfaces_skip_outbound_when_credentials_are_missing(
    fake_slack,
    fake_whatsapp,
    message_store,
):
    slack = SlackPlatformService(
        credentials={
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
        }
    )
    whatsapp = WhatsAppPlatformService(
        {
            "phone_number_id": "phone-contract",
            "api_base_url": f"{fake_whatsapp.api_base}/v21.0",
        }
    )

    await slack.send_message(event=_slack_event(), message="should not send")
    await whatsapp.send_message(event=_whatsapp_event(), message="should not send")

    assert message_store.get_all("SLACK") == []
    assert message_store.get_all("WHATSAPP") == []


async def test_resend_final_answer_contract(fake_resend, message_store):
    service = ResendPlatformService(
        {
            "api_key": "resend-contract-token",
            "from_address": "assistant@example.test",
            "from_name": "Lemma Contract",
            "api_base_url": fake_resend.api_base,
        }
    )

    await service.send_message(event=_resend_event(), message="Contract reply")

    messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    payload = messages[-1]
    assert payload["_method"] == "POST"
    assert payload["_path"] == "/emails"
    assert payload["_authorization"] == "Bearer resend-contract-token"
    assert payload["from"] == "Lemma Contract <assistant@example.test>"
    assert payload["to"] == ["sender@example.test"]
    assert payload["subject"] == "Re: Contract Subject"
    assert payload["headers"] == {
        "In-Reply-To": "<resend-message-1@example.test>",
        "References": "<resend-root@example.test>",
    }
    assert payload["text"] == "Contract reply"


# ---------------------------------------------------------------------------
# Shared platform helpers (app/modules/agent_surfaces/platforms/common.py).
# Every platform adapter/parser routes attachment rendering, webhook URL
# derivation, and provider-error classification through these; the contracts
# above only exercise them incidentally, so cover their branches directly.
# ---------------------------------------------------------------------------


class _ThirdPartyAttachment(BaseModel):
    """A foreign model shape (has ``model_dump`` but is not
    ``SurfaceFileAttachment``) -- distinguishes ``coerce_attachments``'
    ``isinstance`` branch from its ``hasattr(..., "model_dump")`` branch."""

    name: str
    download_url: str | None = None


def test_platform_webhook_url_requires_public_https(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "api_url", "https://public.example.test")
    assert platform_webhook_url(SurfacePlatform.SLACK) == (
        "https://public.example.test/surfaces/webhooks/slack"
    )

    monkeypatch.setattr(settings, "api_url", "http://localhost:8000")
    assert platform_webhook_url(SurfacePlatform.SLACK) is None


def test_coerce_attachments_normalizes_models_dicts_and_mixed_types():
    matching = SurfaceFileAttachment(name="matching.txt")
    foreign = _ThirdPartyAttachment(
        name="foreign.txt", download_url="https://example.test/foreign"
    )
    raw_dict = {"name": "raw.txt"}

    normalized = coerce_attachments(
        [matching, foreign, raw_dict], SurfaceFileAttachment
    )

    assert normalized[0] is matching
    assert normalized[1].name == "foreign.txt"
    assert normalized[1].download_url == "https://example.test/foreign"
    assert normalized[2].name == "raw.txt"


def test_select_attachment_by_download_url_name_and_fallbacks():
    attachments = [
        SurfaceFileAttachment(
            id="a1", name="Report.pdf", download_url="https://example.test/a1"
        ),
        SurfaceFileAttachment(
            id="a2", name="notes.txt", download_url="https://example.test/a2"
        ),
    ]

    by_ref = select_attachment(attachments, ref="a1")
    assert by_ref is not None
    assert by_ref.id == "a1"

    by_url = select_attachment(attachments, download_url="https://example.test/a2")
    assert by_url is not None
    assert by_url.id == "a2"

    by_name = select_attachment(attachments, name="report.pdf")
    assert by_name is not None
    assert by_name.id == "a1"

    ambiguous = select_attachment(
        [
            SurfaceFileAttachment(id="b1", name="dup.txt"),
            SurfaceFileAttachment(id="b2", name="dup.txt"),
        ],
        name="dup.txt",
    )
    assert ambiguous is None

    single = select_attachment([attachments[0]])
    assert single is attachments[0]

    unresolvable = select_attachment(attachments)
    assert unresolvable is None


def test_attachment_tool_hint_covers_every_platform_and_unknown():
    assert attachment_tool_hint("SLACK") is not None
    assert "slack_download_file" in attachment_tool_hint("SLACK")
    assert "teams_download_file" in attachment_tool_hint("TEAMS")
    assert "whatsapp_download_file" in attachment_tool_hint("WHATSAPP")
    assert "telegram_download_file" in attachment_tool_hint("TELEGRAM")
    # Email has no download tool: an inbound attachment is already ingested into
    # pod files by the time the agent sees the message.
    assert attachment_tool_hint("RESEND") is None
    assert attachment_tool_hint("SOME_UNKNOWN_PLATFORM") is None


def test_channel_author_label_falls_back_to_none_when_unattributed():
    assert channel_author_label(None, None) is None
    assert channel_author_label("Jane", None) == "Jane (other participant)"


def test_render_attachment_prompt_block_permalink_hint_and_skips_invalid():
    attachments = [
        {"size": "not-a-number"},  # fails model validation -> skipped
        42,  # neither a model nor a dict -> skipped
        {"content_type": "text/plain"},  # no name/id/download_url -> filtered out
        SurfaceFileAttachment(
            name="via-permalink.pdf",
            permalink="https://example.test/permalink",
            mime_type="",
        ),
    ]

    prompt = render_attachment_prompt_block(
        attachments, platform="SLACK", include_hint=True
    )

    assert "via-permalink.pdf" in prompt
    assert "permalink=https://example.test/permalink" in prompt
    assert "slack_download_file" in prompt

    assert render_attachment_prompt_block([], platform="SLACK") == ""


def test_render_attachment_summary_suffix_lists_details_and_empty_case():
    attachments = [
        SurfaceFileAttachment(
            id="s1",
            name="one.pdf",
            content_type="application/pdf",
            download_url="https://example.test/1",
        ),
    ]

    suffix = render_attachment_summary_suffix(attachments)
    assert suffix.startswith(" | files: ")
    assert "one.pdf" in suffix
    assert "id=s1" in suffix
    assert "download_url=https://example.test/1" in suffix

    assert render_attachment_summary_suffix([]) == ""


def test_detail_label_falls_back_through_content_type_mime_type_and_file_type():
    """A falsy ``content_type`` used to crash straight into a bare
    ``.strip()`` on ``mime_type``, which is ``None`` by default (not ``""``)
    -- any attachment metadata that never set it (a plausible shape from a
    platform that just doesn't report one) raised ``AttributeError`` the
    moment ``render_attachment_summary_suffix``/``render_attachment_prompt_block``
    tried to render it."""
    assert SurfaceFileAttachment(name="no-metadata.bin").detail_label() == ""
    assert (
        SurfaceFileAttachment(name="a.bin", file_type="binary").detail_label()
        == "binary"
    )
    assert (
        SurfaceFileAttachment(
            name="a.pdf", mime_type="application/pdf", file_type="binary"
        ).detail_label()
        == "application/pdf"
    )
    assert (
        SurfaceFileAttachment(
            name="a.pdf", content_type="application/pdf", mime_type="ignored"
        ).detail_label()
        == "application/pdf"
    )


def test_provider_failure_classifies_status_body_and_missing_response():
    class _FakeResponse:
        def __init__(self, status_code, *, json_result=None, json_error=None):
            self.status_code = status_code
            self._json_result = json_result
            self._json_error = json_error

        def json(self):
            if self._json_error is not None:
                raise self._json_error
            return self._json_result

    class _ProviderError(Exception):
        def __init__(self, response):
            super().__init__("provider call failed")
            self.response = response

    no_response_failure = provider_failure(RuntimeError("boom"))
    assert isinstance(no_response_failure, ProviderFailure)
    assert no_response_failure.failure_type == "RuntimeError"
    assert no_response_failure.status_code is None

    named_failure = provider_failure(
        _ProviderError(_FakeResponse(403, json_result={"name": "restricted_api_key"}))
    )
    assert named_failure.status_code == 403
    assert named_failure.provider_error == "restricted_api_key"

    unparseable_failure = provider_failure(
        _ProviderError(_FakeResponse(500, json_error=ValueError("bad body")))
    )
    assert unparseable_failure.status_code == 500
    assert unparseable_failure.provider_error is None


# ---------------------------------------------------------------------------
# Shared email helpers (app/modules/agent_surfaces/platforms/email_*.py).
# Gmail, Outlook, and Resend all route body cleaning, reply threading, and
# display-resource rendering through these pure functions.
# ---------------------------------------------------------------------------


def test_plain_text_from_html_strips_non_text_tags_and_truncates():
    from app.modules.agent_surfaces.platforms.email_text import (
        _MAX_HTML_CHARS,
        plain_text_from_html,
    )

    assert plain_text_from_html(None) == ""

    styled = "<style>body{color:red}</style><p>Hello</p>"
    text = plain_text_from_html(styled)
    assert text == "Hello"
    assert "color:red" not in text

    oversize = "<p>" + ("a" * (_MAX_HTML_CHARS + 10)) + "</p>"
    truncated_text = plain_text_from_html(oversize)
    assert "message truncated" in truncated_text


def test_reply_subject_defaults_and_preserves_existing_prefix():
    from app.modules.agent_surfaces.platforms.email_text import reply_subject

    assert reply_subject(None) == "Reply from Lemma"
    assert reply_subject("  ") == "Reply from Lemma"
    assert reply_subject("Re: Already replied") == "Re: Already replied"
    assert reply_subject("New thread") == "Re: New thread"


def test_looks_forwarded_detects_subject_prefix():
    from app.modules.agent_surfaces.platforms.email_text import looks_forwarded

    assert looks_forwarded(None, subject="Fwd: quarterly numbers") is True
    assert looks_forwarded("no markers here", subject="just a subject") is False


def test_strip_quoted_reply_edge_cases():
    from app.modules.agent_surfaces.platforms.email_text import strip_quoted_reply

    assert strip_quoted_reply("") == ""
    assert strip_quoted_reply("   ") == ""

    # A forward is never trimmed -- the forwarded content is the message.
    forwarded = strip_quoted_reply("please review this", subject="Fwd: doc")
    assert forwarded == "please review this"

    # An "On ... wrote:" marker cuts the quoted original.
    quote_marker_body = (
        "My reply.\n\nOn Mon, Jan 1, 2024, Alice wrote:\n> original text"
    )
    assert strip_quoted_reply(quote_marker_body).strip() == "My reply."

    # "> " quoting that runs to the end of the message is trimmed; the prose
    # written above it survives.
    trailing_quote_body = "Hello there\n> quoted line 1\n> quoted line 2"
    assert strip_quoted_reply(trailing_quote_body) == "Hello there"


def test_inbound_email_text_falls_back_to_html_part():
    from app.modules.agent_surfaces.platforms.email_text import inbound_email_text

    text = inbound_email_text(text=None, html="<p>Hello from HTML</p>")
    assert text == "Hello from HTML"


def test_decode_email_html_data_uri_and_plain_passthrough():
    from app.modules.agent_surfaces.platforms.email_text import decode_email_html

    assert decode_email_html(None) == ""

    base64_uri = "data:text/html;base64," + __import__("base64").b64encode(
        b"<p>Hi</p>"
    ).decode("ascii")
    assert decode_email_html(base64_uri) == "<p>Hi</p>"

    percent_uri = "data:text/plain,Hello%20World"
    assert decode_email_html(percent_uri) == "Hello World"

    # Malformed base64 in a data URI falls back to returning the raw string
    # rather than losing the email entirely.
    malformed_uri = "data:text/html;base64,abc"
    assert decode_email_html(malformed_uri) == malformed_uri

    plain_html = "<p>already plain</p>"
    assert decode_email_html(plain_html) == plain_html


def test_render_email_content_html_and_markdown_fallback(monkeypatch):
    from app.modules.agent_surfaces.platforms import email_render as email_render_module

    plain, html = email_render_module.render_email_content(
        content="<p>Hi</p>", content_type="html"
    )
    assert plain == "Hi"
    assert html == "<p>Hi</p>"

    # With the optional `markdown` dependency unavailable, markdown content
    # falls back to an escaped `<pre>` block rather than rendered HTML.
    monkeypatch.setattr(email_render_module, "markdown_lib", None)
    plain_md, html_md = email_render_module.render_email_content(
        content="a < b && b > c", content_type="markdown"
    )
    assert plain_md == "a < b && b > c"
    assert "<pre>a &lt; b &amp;&amp; b &gt; c</pre>" in html_md
    # Even the fallback is wrapped, so a deployment without the optional
    # dependency still gets a readable width and font rather than the client's.
    assert html_md.startswith("<div style=")


def test_render_email_content_appends_display_resource_plans():
    from app.modules.agent_surfaces.domain.models import (
        SurfaceDisplayAction,
        SurfaceDisplayRenderPlan,
    )
    from app.modules.agent_surfaces.platforms.email_render import render_email_content

    plan = SurfaceDisplayRenderPlan(
        resource_type="record",
        title="Weekly Report",
        summary="Everything is on track.",
        detail_lines=["Revenue: $10k", "Churn: 2%"],
        actions=[SurfaceDisplayAction(label="Open report", url="https://e2e.test/r")],
    )

    plain, html = render_email_content(
        content="See the attached update.",
        content_type="text",
        display_resource_plans=[plan],
    )

    assert "See the attached update." in plain
    assert "Weekly Report" in plain
    assert html is not None
    assert "Weekly Report" in html
    assert "Everything is on track." in html
    assert "Revenue: $10k" in html
    assert "Open report" in html
    assert "https://e2e.test/r" in html


def test_coerce_display_resource_plans_normalizes_mixed_input():
    from pydantic import BaseModel

    from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
    from app.modules.agent_surfaces.platforms.email_render import (
        coerce_display_resource_plans,
    )

    class _ForeignPlan(BaseModel):
        resource_type: str
        title: str

    assert coerce_display_resource_plans(None) == []

    matching = SurfaceDisplayRenderPlan(resource_type="record", title="Direct")
    foreign = _ForeignPlan(resource_type="record", title="Foreign")
    invalid_dict = {"title": "missing resource_type"}
    not_a_plan = 12345

    plans = coerce_display_resource_plans([matching, foreign, invalid_dict, not_a_plan])

    assert len(plans) == 2
    assert plans[0] is matching
    assert plans[1].title == "Foreign"

    # A single non-list value is wrapped, not rejected.
    single = coerce_display_resource_plans(matching)
    assert single == [matching]


def test_guess_content_type_decode_base64_and_file_name_helpers():
    from app.modules.agent_surfaces.platforms.email_attachments import (
        decode_base64_bytes,
        file_name_from_path,
        guess_content_type,
    )

    assert guess_content_type("report.pdf") == "application/pdf"
    assert guess_content_type("unknown.notarealext") == "application/octet-stream"

    assert decode_base64_bytes("", urlsafe=False) == b""
    assert decode_base64_bytes("aGVsbG8=", urlsafe=False) == b"hello"

    assert file_name_from_path("/me/reports/summary.pdf") == "summary.pdf"
    assert file_name_from_path("") == "attachment"


async def test_resolve_outbound_email_attachments_and_urls(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    import app.composition.surface_agent as surface_agent_composition
    from app.modules.agent_surfaces.platforms.email_attachments import (
        append_attachment_links,
        resolve_outbound_email_attachment_urls,
        resolve_outbound_email_attachments,
    )

    small_deps = SimpleNamespace(
        pod_id=uuid4(),
        file_manager=SimpleNamespace(read_file=AsyncMock(return_value=b"hello")),
    )
    inline, links = await resolve_outbound_email_attachments(
        small_deps, ["note.txt"], inline_cap_bytes=1024
    )
    assert inline == [("note.txt", b"hello", "text/plain")]
    assert links == []

    big_deps = SimpleNamespace(
        pod_id=uuid4(),
        file_manager=SimpleNamespace(read_file=AsyncMock(return_value=b"x" * 20)),
    )
    inline_oversize, links_oversize = await resolve_outbound_email_attachments(
        big_deps, ["big.bin"], inline_cap_bytes=5
    )
    assert inline_oversize == []
    assert links_oversize == []

    # A datastore file too large to inline resolves to a signed-URL link
    # instead. `pod_services` is a real DB-backed async context manager in
    # production; fake it here (matching the unit-level Composio email
    # tests' style) so this stays a hermetic test of the size-gating branch.
    fake_entity = SimpleNamespace(name="report.pdf", size_bytes=999)
    fake_file_service = SimpleNamespace(
        get_file_by_path=AsyncMock(return_value=fake_entity),
        create_signed_url=AsyncMock(
            return_value=(
                fake_entity,
                "https://signed.example.test/report.pdf",
                None,
                None,
            )
        ),
    )

    @asynccontextmanager
    async def _fake_pod_services(deps):
        del deps
        yield SimpleNamespace(file=fake_file_service, ctx=None)

    monkeypatch.setattr(surface_agent_composition, "pod_services", _fake_pod_services)

    datastore_deps = SimpleNamespace(pod_id=uuid4())
    inline_link, links_link = await resolve_outbound_email_attachments(
        datastore_deps, ["/me/reports/report.pdf"], inline_cap_bytes=10
    )
    assert inline_link == []
    assert links_link == [("report.pdf", "https://signed.example.test/report.pdf")]

    resolved, unresolved = await resolve_outbound_email_attachment_urls(
        big_deps, ["work.bin"]
    )
    assert resolved == []
    assert unresolved == ["work.bin"]

    assert append_attachment_links("body", []) == "body"
    assert (
        append_attachment_links("body", [("f.pdf", "https://x.test/f.pdf")])
        == "body\n\nf.pdf: https://x.test/f.pdf"
    )


def test_parse_email_identity_and_read_helpers_handle_unrecognized_shapes():
    from app.modules.agent_surfaces.platforms.email_identity import (
        _read_email_name,
        parse_email_identity,
    )

    # Neither a string nor a dict: falls through to the fallback identity with
    # no display name resolvable from `value` itself.
    identity = parse_email_identity(12345, fallback_email="fallback@example.test")
    assert identity.email == "fallback@example.test"
    assert identity.display_name is None

    # `_read_email_name` itself, for a shape `parse_email_identity` never
    # forwards to it (an unresolved email short-circuits before the name is
    # read) -- covered directly instead.
    assert _read_email_name(12345) is None
