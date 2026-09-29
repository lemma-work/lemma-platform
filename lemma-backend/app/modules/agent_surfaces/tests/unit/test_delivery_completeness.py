"""A message that leaves Lemma either arrives, or says why it did not.

One file for the egress fixes that share that theme, because they share a shape:
a platform call that failed, or could not be made, and something that used to
report success anyway -- a send with no token that logged at debug and returned,
a second question that failed and made the caller resend the first, a photo
Telegram would not take as a photo, a transient 503 nobody retried.

Each test is a platform doing the failing thing over a doubled transport; none of
them needs a database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.envelope import PartDelivery
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalButton,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms import rendering
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    _question_select_element,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.slack.streaming import (
    SlackStreamSurface,
    remove_processing_reaction,
)
from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram import outbound
from app.modules.agent_surfaces.platforms.telegram.callback_token_store import (
    _CALLBACK_TOKEN_TTL_SECONDS,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TelegramApiError,
    TelegramClient,
)
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)
from app.modules.agent_surfaces.platforms.whatsapp import media as whatsapp_media
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
)
from app.modules.agent_surfaces.platforms.whatsapp.payloads import (
    build_whatsapp_approval_interactive,
    build_whatsapp_interactive,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)

pytestmark = pytest.mark.asyncio

_NO_WAIT = RetryPolicy(max_attempts=3, base_delay=0.0, max_delay=0.0)


def _events(caplog, name: str) -> list:
    return [record for record in caplog.records if name in str(record.getMessage())]


def _plan(*questions: SurfaceQuestion) -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan(
        title="Questions", questions=list(questions), callback_id="conv|tool"
    )


def _question(header: str, text: str, *labels: str) -> SurfaceQuestion:
    return SurfaceQuestion(
        header=header,
        question=text,
        options=[SurfaceQuestionOption(label=label) for label in labels],
    )


# --- WhatsApp -----------------------------------------------------------------


def _wa_event(**target: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="WHATSAPP",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="phone-1",
        external_thread_id="15551234567@phone-1",
        external_message_id="wamid.in",
        sender_phone="15551234567",
        message_text="hi",
        reply_target={
            "phone_number_id": "phone-1",
            "sender_wa_id": "15551234567",
            **target,
        },
    )


def _wa_service(**credentials: str) -> WhatsAppPlatformService:
    return WhatsAppPlatformService(
        {
            "access_token": "t",
            "phone_number_id": "phone-1",
            "api_base_url": "http://x/v21.0",
            **credentials,
        }
    )


class _ScriptedWhatsAppClient(WhatsAppClient):
    """The real client over a scripted transport, so the retry is the shipping one."""

    def __init__(self, outcomes: list[object]) -> None:
        super().__init__(
            access_token="t", api_base="http://x/v21.0", retry_policy=_NO_WAIT
        )
        self.outcomes = list(outcomes)
        self.posts: list[dict] = []

    async def _post_json(self, url, *, json, method):
        self.posts.append(json)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


async def test_whatsapp_retries_a_transient_failure():
    """WhatsApp was the one platform with no retry: a single 503 lost the answer."""
    client = _ScriptedWhatsAppClient(
        [
            WhatsAppApiError(method="messages", status_code=503),
            WhatsAppApiError(method="messages", status_code=429),
            {"messages": [{"id": "wamid.out"}]},
        ]
    )

    message_id = await client.send_message_payload(
        phone_number_id="phone-1", payload={"type": "text"}
    )

    assert message_id == "wamid.out"
    assert len(client.posts) == 3


async def test_whatsapp_does_not_retry_a_permanent_failure():
    client = _ScriptedWhatsAppClient(
        [WhatsAppApiError(method="messages", status_code=400)]
    )

    with pytest.raises(WhatsAppApiError):
        await client.send_message_payload(
            phone_number_id="phone-1", payload={"type": "text"}
        )

    assert len(client.posts) == 1


async def test_whatsapp_does_not_retry_a_typing_indicator():
    """A keep-alive that fails is replaced by the next one; a retry only delays."""
    client = _ScriptedWhatsAppClient(
        [WhatsAppApiError(method="messages", status_code=503)]
    )

    with pytest.raises(WhatsAppApiError):
        await client.mark_read_and_typing(phone_number_id="phone-1", message_id="m")

    assert len(client.posts) == 1


class _RecordingWhatsAppClient(WhatsAppClient):
    """Records what the service asks of it; fails the calls it is told to."""

    def __init__(self, *, fail_interactive_after: int | None = None) -> None:
        super().__init__(access_token="t", api_base="http://x/v21.0")
        self.fail_interactive_after = fail_interactive_after
        self.interactives: list[dict] = []
        self.payloads: list[dict] = []
        self.fail_payload_number: int | None = None

    async def send_interactive(self, *, phone_number_id, to, interactive):
        if (
            self.fail_interactive_after is not None
            and len(self.interactives) >= self.fail_interactive_after
        ):
            raise WhatsAppApiError(method="messages", status_code=500)
        self.interactives.append(interactive)
        return "wamid.q"

    async def send_message_payload(self, *, phone_number_id, payload):
        self.payloads.append(payload)
        if self.fail_payload_number == len(self.payloads):
            raise WhatsAppApiError(method="messages", status_code=500)
        return "wamid.out"


async def test_whatsapp_with_no_token_raises_instead_of_reporting_a_send():
    """It logged at debug and returned, and the caller recorded NATIVE."""
    service = _wa_service(access_token="")

    with pytest.raises(AgentSurfaceError) as raised:
        await service.send_message(_wa_event(), "hello")

    assert "access_token" in str(raised.value)


async def test_whatsapp_with_no_recipient_raises_naming_it():
    service = _wa_service()
    event = _wa_event(sender_wa_id="")
    event.sender_phone = None

    with pytest.raises(AgentSurfaceError) as raised:
        await service.send_message(event, "hello")

    assert "recipient" in str(raised.value)


async def test_whatsapp_resource_with_no_target_raises():
    service = _wa_service(access_token="")
    plan = SurfaceDisplayRenderPlan(title="Report", resource_type="FILE", actions=[])

    with pytest.raises(AgentSurfaceError):
        await service._render_resource(_wa_event(), plan)


async def test_whatsapp_reports_a_card_as_a_card_and_a_fallback_as_text():
    service = _wa_service()
    service._client = _RecordingWhatsAppClient()
    with_link = SurfaceDisplayRenderPlan(
        title="Report",
        resource_type="FILE",
        actions=[SurfaceDisplayAction(label="Open", url="https://lemma.test/r")],
    )
    without_link = SurfaceDisplayRenderPlan(
        title="Report", resource_type="FILE", actions=[]
    )

    assert await service._render_resource(_wa_event(), with_link) is True
    assert await service._render_resource(_wa_event(), without_link) is False


async def test_a_second_question_that_fails_does_not_resend_the_first(caplog):
    """The double-send.

    One interactive message per question: when the second failed, the raise sent
    the caller to its text fallback, which resent *every* question -- so the
    first appeared twice, once as buttons and once as words.
    """
    service = _wa_service()
    client = _RecordingWhatsAppClient(fail_interactive_after=1)
    service._client = client
    plan = _plan(
        _question("size", "Which size?", "Small", "Large"),
        _question("color", "Which colour?", "Red", "Blue"),
    )

    # Delivered, but only the first as buttons: DEGRADED is what arms a typed
    # answer to the rest, where True would say every question is tappable.
    assert await service._render_choices(_wa_event(), plan) is PartDelivery.DEGRADED

    assert len(client.interactives) == 1
    assert len(client.payloads) == 1
    text = client.payloads[0]["text"]["body"]
    assert "Which colour?" in text
    assert "Which size?" not in text, "the first question is not asked twice"
    assert _events(caplog, "questions_partially_delivered")


async def test_a_first_question_that_fails_still_reaches_the_text_fallback():
    """Nothing went out, so the fallback resending everything is not a duplicate."""
    service = _wa_service()
    service._client = _RecordingWhatsAppClient(fail_interactive_after=0)
    plan = _plan(_question("size", "Which size?", "Small", "Large"))

    with pytest.raises(WhatsAppApiError):
        await service._render_choices(_wa_event(), plan)


async def test_a_long_message_that_fails_part_way_says_how_far_it_got(caplog):
    service = _wa_service()
    client = _RecordingWhatsAppClient()
    client.fail_payload_number = 2
    service._client = client

    with pytest.raises(WhatsAppApiError):
        await service.send_message(_wa_event(), ("word " * 900).strip())

    partial = _events(caplog, "message_partially_delivered")
    assert partial and partial[0].levelname == "WARNING"


def _option(label: str, description: str = "") -> SurfaceQuestionOption:
    return SurfaceQuestionOption(label=label, description=description)


async def test_whatsapp_list_rows_keep_their_descriptions():
    question = SurfaceQuestion(
        header="plan",
        question="Which plan?",
        options=[_option(f"Plan {i}", f"What plan {i} means") for i in range(5)],
    )

    interactive = build_whatsapp_interactive("conv|tool", question)

    rows = interactive["action"]["sections"][0]["rows"]
    assert rows[0]["description"] == "What plan 0 means"


async def test_whatsapp_reply_buttons_carry_their_descriptions_in_the_body():
    """A reply button is a title and nothing else, so the notes ride the body."""
    question = SurfaceQuestion(
        header="how",
        question="How should I proceed?",
        options=[_option("Refund", "Money back in 3 days"), _option("Credit")],
    )

    interactive = build_whatsapp_interactive("conv|tool", question)

    body = interactive["body"]["text"]
    assert body.startswith("How should I proceed?")
    assert "Refund: Money back in 3 days" in body
    assert len(body) <= 1024


async def test_whatsapp_approval_keeps_the_action_line_when_the_reason_is_long():
    """Cutting the tail of a 1024-character body dropped the action first."""
    plan = SurfaceApprovalRenderPlan(
        title="Delete order 42",
        reason="Because " + "the customer asked. " * 200,
        action_summary="exec_command(cmd=lemma records delete orders --id 42)",
        callback_id="conv|tool",
        buttons=[
            SurfaceApprovalButton(label="Approve", decision="APPROVE_ONCE"),
            SurfaceApprovalButton(label="Deny", decision="DENY"),
        ],
    )

    interactive = build_whatsapp_approval_interactive(plan)

    body = interactive["body"]["text"]
    assert len(body) <= 1024
    assert "lemma records delete orders --id 42" in body
    assert "Delete order 42" in body


class _FakeMediaClient:
    """Enough of ``WhatsAppClient`` to walk the media ladder."""

    has_credentials = True

    def __init__(self, *, reject: set[str] = frozenset(), status: int = 400) -> None:
        self.reject = reject
        self.status = status
        self.kinds: list[str] = []

    async def upload_media(self, **_kwargs):
        return "media-1"

    async def send_media(self, *, send_type, **_kwargs):
        self.kinds.append(send_type)
        if send_type in self.reject:
            raise WhatsAppApiError(method="messages", status_code=self.status)
        return "wamid.media"


async def _send_media(client, mime="image/webp"):
    return await whatsapp_media.send_file(
        client,
        phone_number_id="phone-1",
        recipient_wa_id="15551234567",
        file_name="chart.webp",
        file_bytes=b"RIFF",
        mime_type=mime,
    )


async def test_a_rejected_image_is_retried_once_as_a_document():
    """Meta takes JPEG and PNG as images; a document takes what upload accepted."""
    client = _FakeMediaClient(reject={"image"})

    assert await _send_media(client) is True

    assert client.kinds == ["image", "document"]


async def test_a_document_that_is_rejected_is_not_retried_again():
    client = _FakeMediaClient(reject={"image", "document"})

    with pytest.raises(WhatsAppApiError):
        await _send_media(client)

    assert client.kinds == ["image", "document"]


async def test_a_server_error_is_not_mistaken_for_a_rejected_type():
    client = _FakeMediaClient(reject={"image"}, status=503)

    with pytest.raises(WhatsAppApiError):
        await _send_media(client)

    assert client.kinds == ["image"]


# --- Telegram -----------------------------------------------------------------


def _tg_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="555",
        external_thread_id="555",
        external_message_id="10",
        message_text="hi",
        is_dm=True,
        reply_target={"chat_id": "555"},
    )


class _ScriptedTelegramClient(TelegramClient):
    """The real client with its two transport verbs scripted."""

    def __init__(
        self,
        *,
        multipart: list[object] | None = None,
        call_error: Exception | None = None,
    ) -> None:
        super().__init__(bot_token="T", api_base="http://fake/bot")
        self.multipart = list(multipart or [])
        self.call_error = call_error
        self.uploads: list[dict] = []

    async def call(self, method, payload, *, client=None):
        if self.call_error is not None:
            raise self.call_error
        return {"ok": True, "result": {}}

    async def call_multipart(self, method, *, fields, files, timeout=None):
        self.uploads.append(
            {"method": method, "fields": fields, "files": files, "timeout": timeout}
        )
        outcome = self.multipart.pop(0) if self.multipart else {"ok": True}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _TelegramService(TelegramPlatformService):
    """The real service, recording the messages it is asked to send.

    `send_message` is the seam `_render_choices` sends each question through; a
    subclass records it without patching the subject from outside.
    """

    def __init__(self, client: TelegramClient, *, fail_after: int | None = None):
        super().__init__({"bot_token": "T", "api_base_url": "http://fake/bot"})
        self._client = client
        self._retry_policy = _NO_WAIT
        self.fail_after = fail_after
        self.sent: list[str] = []

    async def send_message(self, event, message, metadata=None):
        # Only the messages that carry buttons fail: the plain-text fallback for
        # what is left is the thing under test, and it has to be able to land.
        carries_buttons = bool((metadata or {}).get("reply_markup"))
        if (
            carries_buttons
            and self.fail_after is not None
            and len(self.sent) >= self.fail_after
        ):
            raise TelegramApiError(method="sendMessage", status_code=500)
        self.sent.append(message)


def _tg_service(**client_kwargs) -> _TelegramService:
    return _TelegramService(_ScriptedTelegramClient(**client_kwargs))


@pytest.fixture
def callback_tokens():
    """Real callback tokens, over an in-memory Redis instead of the network."""
    import fakeredis

    from app.core.config import settings
    from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
    from app.modules.agent_surfaces.platforms.telegram import callback_token_store

    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:cb", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    previous = callback_token_store._token_store
    callback_token_store._token_store = cache
    yield cache
    callback_token_store._token_store = previous


async def test_a_telegram_second_question_that_fails_does_not_resend_the_first(
    callback_tokens, caplog
):
    service = _TelegramService(_ScriptedTelegramClient(), fail_after=1)
    plan = _plan(
        _question("size", "Which size?", "Small", "Large"),
        _question("color", "Which colour?", "Red", "Blue"),
    )

    assert await service._render_choices(_tg_event(), plan) is PartDelivery.DEGRADED

    assert service.sent[0] == "Which size?"
    assert "Which colour?" in service.sent[1]
    assert "Which size?" not in service.sent[1]
    assert _events(caplog, "questions_partially_delivered")


async def test_telegram_keeps_option_descriptions_in_the_question():
    question = SurfaceQuestion(
        header="how",
        question="How should I proceed?",
        options=[_option("Refund", "Money back in 3 days"), _option("Credit")],
    )

    text = outbound.question_with_option_notes(question)

    assert text.startswith("How should I proceed?")
    assert "Refund — Money back in 3 days" in text
    assert "Credit" not in text.split("Refund", 1)[0]


async def test_telegram_uploads_go_through_the_shared_client():
    """They went to raw httpx: no SSRF guard, no retry, no caption limit."""
    service = _tg_service()

    assert await service.send_file_bytes(
        _tg_event(),
        file_name="report.pdf",
        file_bytes=b"%PDF",
        mime_type="application/pdf",
        caption="c" * 2000,
    )

    upload = service._client.uploads[0]
    assert upload["method"] == "sendDocument"
    assert len(upload["fields"]["caption"]) == 1024
    assert upload["timeout"] and upload["timeout"] > 10


async def test_a_photo_telegram_refuses_is_retried_as_a_document(caplog):
    service = _tg_service(
        multipart=[
            TelegramApiError(
                method="sendPhoto", status_code=400, description="PHOTO_INVALID"
            )
        ]
    )

    assert await service.send_file_bytes(
        _tg_event(), file_name="wide.png", file_bytes=b"\x89PNG", mime_type="image/png"
    )

    assert [u["method"] for u in service._client.uploads] == [
        "sendPhoto",
        "sendDocument",
    ]
    assert _events(caplog, "media_type_rejected")


async def test_a_telegram_upload_is_retried_when_the_api_is_briefly_down():
    service = _tg_service(
        multipart=[TelegramApiError(method="sendDocument", status_code=502)]
    )

    assert await service.send_file_bytes(
        _tg_event(),
        file_name="r.pdf",
        file_bytes=b"x",
        mime_type="application/pdf",
    )

    assert len(service._client.uploads) == 2


async def test_a_telegram_voice_note_uses_the_shared_client():
    service = _tg_service()

    assert await service.send_voice_bytes(
        _tg_event(), file_name="a.ogg", audio_bytes=b"Ogg", mime_type="audio/ogg"
    )

    upload = service._client.uploads[0]
    assert upload["method"] == "sendVoice"
    assert "voice" in upload["files"]


async def test_a_dead_telegram_api_lets_the_typing_indicator_say_so():
    """It swallowed the error, so `show_typing` could never answer False.

    On a dead API the refresh loop ran for its full fifteen minutes.
    """
    service = _tg_service(
        call_error=TelegramApiError(method="sendChatAction", status_code=502)
    )

    with pytest.raises(TelegramApiError):
        await service.add_processing_indicator(_tg_event())


async def test_telegram_buttons_outlive_the_hour_a_pause_can_last():
    assert _CALLBACK_TOKEN_TTL_SECONDS >= 24 * 60 * 60


async def test_a_telegram_resource_is_a_card_only_when_it_has_a_link():
    service = _tg_service()
    linked = SurfaceDisplayRenderPlan(
        title="R",
        resource_type="FILE",
        actions=[SurfaceDisplayAction(label="Open", url="https://lemma.test/r")],
    )
    bare = SurfaceDisplayRenderPlan(title="R", resource_type="FILE", actions=[])

    assert await service._render_resource(_tg_event(), linked) is True
    assert await service._render_resource(_tg_event(), bare) is False


# --- Slack --------------------------------------------------------------------


def _slack_event(**target: str) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="T1",
        external_channel_id="C1",
        external_thread_id="100.0",
        external_message_id="100.5",
        message_text="hi",
        reply_target={"channel": "C1", "thread_ts": "100.0", **target},
    )


async def test_slack_with_no_token_raises_instead_of_reporting_a_send():
    service = SlackPlatformService(credentials={})

    with pytest.raises(AgentSurfaceError) as raised:
        await service.send_message(event=_slack_event(), message="hello")

    assert "access_token" in str(raised.value)


async def test_slack_with_no_channel_raises_for_a_resource_too():
    service = SlackPlatformService(credentials={"access_token": "xoxb-1"})
    plan = SurfaceDisplayRenderPlan(title="R", resource_type="FILE", actions=[])

    with pytest.raises(AgentSurfaceError) as raised:
        await service._render_resource(event=_slack_event(channel=""), render_plan=plan)

    assert "channel" in str(raised.value)


async def test_the_eyes_come_off_the_message_once_it_is_answered(monkeypatch):
    """Nothing ever removed it, so every answered message stayed marked."""
    removed: list[dict] = []
    posted: list[dict] = []

    async def reactions_remove(self, **kwargs):
        removed.append(kwargs)
        return {"ok": True}

    async def post(self, **kwargs):
        posted.append(kwargs)
        return {"ok": True, "ts": "1.0"}

    monkeypatch.setattr(AsyncWebClient, "reactions_remove", reactions_remove)
    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)
    service = SlackPlatformService(credentials={"access_token": "xoxb-1"})

    await service.send_message(event=_slack_event(), message="The answer.")

    assert posted
    assert removed == [{"channel": "C1", "name": "eyes", "timestamp": "100.5"}]


async def test_an_ephemeral_reply_does_not_take_the_reaction_off(monkeypatch):
    removed: list[dict] = []

    async def reactions_remove(self, **kwargs):
        removed.append(kwargs)

    monkeypatch.setattr(AsyncWebClient, "reactions_remove", reactions_remove)
    monkeypatch.setattr(
        AsyncWebClient, "chat_postEphemeral", AsyncMock(return_value={"ok": True})
    )
    service = SlackPlatformService(credentials={"access_token": "xoxb-1"})

    await service.send_message(
        event=_slack_event(), message="Ask an admin.", metadata={"ephemeral_to": "U9"}
    )

    assert removed == []


async def test_a_reaction_that_is_already_gone_is_not_an_error(monkeypatch):
    async def reactions_remove(self, **kwargs):
        raise SlackApiError("no_reaction", {"error": "no_reaction"})

    monkeypatch.setattr(AsyncWebClient, "reactions_remove", reactions_remove)

    await remove_processing_reaction({"access_token": "xoxb-1"}, _slack_event())


async def test_an_assistant_dm_has_no_reaction_to_remove(monkeypatch):
    removed = AsyncMock()
    monkeypatch.setattr(AsyncWebClient, "reactions_remove", removed)
    event = _slack_event()
    event.is_dm = True

    await remove_processing_reaction(
        {"access_token": "xoxb-1", "scopes": ["assistant:write"]}, event
    )

    removed.assert_not_awaited()


async def test_a_timeout_closing_a_slack_stream_is_a_warning_and_a_fallback(
    monkeypatch, caplog
):
    """`SlackApiError` only: a timeout escaped and the stream was left open."""

    async def append(self, **kwargs):
        raise TimeoutError("slack did not answer")

    monkeypatch.setattr(AsyncWebClient, "chat_appendStream", append)
    surface = SlackStreamSurface(credentials={"access_token": "xoxb-1"})

    finished = await surface.finish_progress(
        _slack_event(), {"ts": "2.0", "channel": "C1", "stream": True}, "The answer."
    )

    assert finished is False
    stopped = _events(caplog, "slack_finish_progress_stop_stream")
    assert stopped and stopped[0].levelname == "WARNING"
    assert "TimeoutError" in stopped[0].getMessage()


async def test_slack_select_options_keep_their_descriptions():
    question = SurfaceQuestion(
        header="how",
        question="How?",
        options=[_option("Refund", "Money back in 3 days"), _option("Credit")],
    )

    element = _question_select_element(question)

    assert element["options"][0]["description"] == {
        "type": "plain_text",
        "text": "Money back in 3 days",
    }
    assert "description" not in element["options"][1]


async def test_slack_cards_are_authored_with_the_agents_icon(monkeypatch):
    posted: list[dict] = []

    async def post(self, **kwargs):
        posted.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(AsyncWebClient, "chat_postMessage", post)
    service = SlackPlatformService(
        credentials={"access_token": "xoxb-1", "scopes": ["chat:write.customize"]}
    )
    metadata = {
        "agent_display_name": "Ada",
        "agent_icon_url": "https://cdn.test/ada.png",
    }
    approval = SurfaceApprovalRenderPlan(
        title="Delete",
        callback_id="conv|tool",
        buttons=[SurfaceApprovalButton(label="Approve", decision="APPROVE_ONCE")],
    )
    resource = SurfaceDisplayRenderPlan(title="R", resource_type="FILE", actions=[])

    await service._render_decision(
        event=_slack_event(), approval_plan=approval, metadata=metadata
    )
    await service._render_choices(
        event=_slack_event(),
        question_plan=_plan(_question("a", "A?", "x", "y")),
        metadata=metadata,
    )
    await service._render_resource(
        event=_slack_event(), render_plan=resource, metadata=metadata
    )

    assert len(posted) == 3
    for kwargs in posted:
        assert kwargs["username"] == "Ada"
        assert kwargs["icon_url"] == "https://cdn.test/ada.png"


# --- Teams --------------------------------------------------------------------


def _teams_event(**overrides) -> ParsedInboundSurfaceEvent:
    fields = {
        "platform": "TEAMS",
        "conversation_type": ConversationType.EXTERNAL_GROUP,
        "tenant_id": "tenant-1",
        "external_channel_id": "19:c",
        "external_thread_id": "1",
        "message_text": "hi",
        "reply_target": {"conversation_id": "conv-1"},
    }
    fields.update(overrides)
    return ParsedInboundSurfaceEvent(**fields)


class _RecordingTeamsAdapter(TeamsSurfaceAdapter):
    """The real adapter with the token and the transport it posts through scripted."""

    def __init__(self, *, token: str | None = "bot-token", fail_on_post: int = 0):
        super().__init__()
        self.token = token
        self.fail_on_post = fail_on_post
        self.posted: list[dict] = []

    async def _get_bot_token(self, tenant_id):
        return self.token

    async def _post_activity(self, url, *, token, body):
        if self.fail_on_post == len(self.posted) + 1:
            raise TimeoutError("teams did not answer")
        self.posted.append(body)


async def test_teams_with_no_conversation_raises_instead_of_reporting_a_send():
    adapter = _RecordingTeamsAdapter()

    with pytest.raises(AgentSurfaceError) as raised:
        await adapter.send_message(
            credentials={}, event=_teams_event(reply_target={}), message="hello"
        )

    assert "conversation_id" in str(raised.value)
    assert adapter.posted == []


async def test_teams_with_no_token_raises_naming_it():
    adapter = _RecordingTeamsAdapter(token=None)

    with pytest.raises(AgentSurfaceError) as raised:
        await adapter.send_message(
            credentials={}, event=_teams_event(), message="hello"
        )

    assert "bot_token" in str(raised.value)


async def test_teams_resource_with_no_tenant_raises():
    adapter = _RecordingTeamsAdapter()
    plan = SurfaceDisplayRenderPlan(title="R", resource_type="FILE", actions=[])

    with pytest.raises(AgentSurfaceError):
        await adapter._render_resource(
            credentials={}, event=_teams_event(tenant_id=None), render_plan=plan
        )


async def test_teams_reports_a_card_as_a_card():
    """It answered `None`, so every card was recorded as DEGRADED."""
    adapter = _RecordingTeamsAdapter()
    plan = SurfaceDisplayRenderPlan(title="R", resource_type="FILE", actions=[])

    rendered = await adapter._render_resource(
        credentials={}, event=_teams_event(), render_plan=plan
    )

    assert rendered is True
    assert adapter.posted[0]["attachments"]


async def test_teams_splits_a_message_longer_than_it_will_take():
    adapter = _RecordingTeamsAdapter()
    message = "\n\n".join(f"Paragraph {i}. " + "word " * 500 for i in range(20))

    await adapter.send_message(credentials={}, event=_teams_event(), message=message)

    assert len(adapter.posted) > 1
    assert all(len(body["text"]) <= 20_000 for body in adapter.posted)
    assert "Paragraph 19." in adapter.posted[-1]["text"]


async def test_a_teams_chunk_that_fails_says_how_far_it_got(caplog):
    adapter = _RecordingTeamsAdapter(fail_on_post=2)
    message = "\n\n".join("word " * 3000 for _ in range(4))

    with pytest.raises(TimeoutError):
        await adapter.send_message(
            credentials={}, event=_teams_event(), message=message
        )

    assert _events(caplog, "teams_message_partially_delivered")


# --- Resend -------------------------------------------------------------------


class _FakeHttpClient:
    """Scripted ``httpx.AsyncClient`` that records how it was built and called."""

    instances: list["_FakeHttpClient"] = []
    outcomes: list[object] = []

    def __init__(self, *args, **kwargs) -> None:
        self.timeout = kwargs.get("timeout")
        self.calls: list[dict] = []
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, *, json, headers):
        self.calls.append({"url": url, "json": json, "headers": headers})
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _http_response(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status,
        json=body or {},
        request=httpx.Request("POST", "https://api.resend.com/emails"),
    )


async def _resend_send(monkeypatch, outcomes: list[object]):
    from app.modules.agent_surfaces.platforms.resend import service as resend_service

    _FakeHttpClient.instances = []
    _FakeHttpClient.outcomes = list(outcomes)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeHttpClient)
    service = resend_service.ResendPlatformService(
        {"api_key": "re_test", "from_address": "a@ops.test"}
    )
    service._retry_policy = _NO_WAIT
    return await service._send_email(
        recipient_email="bob@example.com",
        subject="Hello",
        in_reply_to=None,
        references=[],
        content="hi",
        content_type="markdown",
        attachments=[("big.bin", b"x" * 1024, "application/octet-stream")],
    )


async def test_an_email_send_is_given_time_to_upload_its_attachments(monkeypatch):
    """httpx's default is five seconds; attachments can be ~37 MB encoded."""
    result = await _resend_send(monkeypatch, [_http_response(200, {"id": "e1"})])

    assert result == {"id": "e1"}
    timeout = _FakeHttpClient.instances[0].timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read >= 60


async def test_an_email_send_retries_a_transient_failure_with_one_idempotency_key(
    monkeypatch,
):
    result = await _resend_send(
        monkeypatch,
        [
            httpx.ConnectTimeout("no route"),
            _http_response(503),
            _http_response(200, {"id": "e1"}),
        ],
    )

    assert result == {"id": "e1"}
    keys = {
        call["headers"]["Idempotency-Key"]
        for client in _FakeHttpClient.instances
        for call in client.calls
    }
    assert len(_FakeHttpClient.instances) == 3
    assert len(keys) == 1, "a retry of an accepted request must not be a second email"


async def test_an_email_send_does_not_retry_a_rejection(monkeypatch):
    with pytest.raises(httpx.HTTPStatusError):
        await _resend_send(monkeypatch, [_http_response(422)])

    assert len(_FakeHttpClient.instances) == 1


# --- code fences across chunks -----------------------------------------------


async def test_a_code_block_cut_by_a_chunk_boundary_is_closed_and_reopened():
    """Cut mid-fence, the first half swallowed the rest of the message."""
    body = "\n".join(f"line {i} = {i}" for i in range(40))
    text = f"Here is the code:\n\n```python\n{body}\n```\n\nThat is all."

    chunks = rendering.chunk_text(text, limit=150)

    assert len(chunks) > 2
    assert all(len(chunk) <= 150 for chunk in chunks)
    for chunk in chunks:
        assert chunk.count("```") % 2 == 0, "every chunk is balanced"
    inside = [chunk for chunk in chunks if "line " in chunk]
    assert all(chunk.startswith("```python") for chunk in inside[1:])
    assert chunks[-1].endswith("That is all.")


async def test_text_without_fences_is_split_as_it_always_was():
    assert rendering.chunk_text("a b c", limit=100) == ["a b c"]
    chunks = rendering.chunk_text("word " * 100, limit=50)
    assert all(len(chunk) <= 50 for chunk in chunks)


async def test_a_fence_that_closes_at_a_boundary_is_not_left_empty():
    text = "x" * 50 + "\n```\ncode\n```\n" + "y" * 50

    chunks = rendering.chunk_text(text, limit=100)

    assert all(chunk.count("```") % 2 == 0 for chunk in chunks)
    assert not any(chunk.strip() == "```" for chunk in chunks)
