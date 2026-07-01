from __future__ import annotations

from app.modules.agent_surfaces.domain.models import (
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.slack.service import _question_blocks
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.teams.adapter import _teams_question_card
from app.modules.agent_surfaces.platforms.teams.parser import (
    TEAMS_FORM_CALLBACK_KEY,
    TeamsMessageParser,
)
from app.modules.agent_surfaces.api.controllers.webhook_controller import (
    _decode_webhook_payload,
)


def _plan() -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan(
        title="A few quick questions",
        callback_id="conv-1|tool-1",
        questions=[
            SurfaceQuestion(
                header="country",
                question="Which country?",
                options=[
                    SurfaceQuestionOption(label="US", recommended=True),
                    SurfaceQuestionOption(label="CA"),
                ],
            ),
            SurfaceQuestion(
                header="tags",
                question="Which tags?",
                multi_select=True,
                options=[
                    SurfaceQuestionOption(label="a"),
                    SurfaceQuestionOption(label="b"),
                ],
            ),
        ],
    )


# ── Slack render ────────────────────────────────────────────────────────────


def test_slack_question_blocks_keys_by_header_and_carries_callback():
    blocks = _question_blocks(_plan())
    selects = {
        b["block_id"]: b
        for b in blocks
        if b.get("type") == "input"
        and b["element"]["type"] in ("static_select", "multi_static_select")
    }
    assert selects["country"]["element"]["type"] == "static_select"
    assert selects["tags"]["element"]["type"] == "multi_static_select"
    # option values are the labels; recommended is annotated
    country_opts = selects["country"]["element"]["options"]
    assert {o["value"] for o in country_opts} == {"US", "CA"}
    assert any("recommended" in o["text"]["text"] for o in country_opts)
    # an optional "Other" free-text input is added per question
    other_ids = {
        b["block_id"] for b in blocks if b.get("type") == "input"
    } & {"country__other", "tags__other"}
    assert other_ids == {"country__other", "tags__other"}
    submit = [b for b in blocks if b.get("type") == "actions"][0]["elements"][0]
    assert submit["action_id"] == "lemma_form_submit"
    assert submit["value"] == "conv-1|tool-1"


# ── Teams render ──────────────────────────────────────────────────────────────


def test_teams_question_card_keys_by_header_and_carries_callback():
    card = _teams_question_card(_plan())
    choice_sets = {
        el["id"]: el for el in card["body"] if el.get("type") == "Input.ChoiceSet"
    }
    assert choice_sets["country"]["choices"][0]["value"] == "US"
    assert choice_sets["tags"]["isMultiSelect"] is True
    # an "Other" text input is added per question
    text_ids = {el["id"] for el in card["body"] if el.get("type") == "Input.Text"}
    assert text_ids == {"country__other", "tags__other"}
    submit = card["actions"][0]
    assert submit["type"] == "Action.Submit"
    assert submit["data"][TEAMS_FORM_CALLBACK_KEY] == "conv-1|tool-1"


# ── Slack interaction parse ───────────────────────────────────────────────────


def test_slack_parse_interaction_extracts_values():
    payload = {
        "type": "block_actions",
        "user": {"id": "U1"},
        "team": {"id": "T1"},
        "channel": {"id": "C1"},
        "container": {"message_ts": "123.45"},
        "message": {"ts": "123.45", "thread_ts": "100.0"},
        "actions": [
            {
                "action_id": "lemma_form_submit",
                "value": "conv-1|tool-1",
                "action_ts": "124.0",
            }
        ],
        "state": {
            "values": {
                "email": {"email": {"type": "plain_text_input", "value": "a@b.com"}},
                "country": {
                    "country": {
                        "type": "static_select",
                        "selected_option": {"value": "US"},
                    }
                },
                "tags": {
                    "tags": {
                        "type": "multi_static_select",
                        "selected_options": [{"value": "x"}, {"value": "y"}],
                    }
                },
            }
        },
    }
    interaction = SlackMessageParser().parse_interaction(payload)
    assert interaction is not None
    assert interaction.callback_id == "conv-1|tool-1"
    assert interaction.values == {
        "email": "a@b.com",
        "country": "US",
        "tags": ["x", "y"],
    }
    assert interaction.external_user_id == "U1"
    assert interaction.reply_target == {"channel": "C1", "thread_ts": "100.0"}
    # A normal message event is not an interaction.
    assert SlackMessageParser().parse_interaction({"type": "event_callback"}) is None


# ── Teams interaction parse ───────────────────────────────────────────────────


def test_teams_parse_interaction_extracts_values():
    payload = {
        "type": "message",
        "id": "act-9",
        "from": {"id": "29:u"},
        "conversation": {"id": "19:conv"},
        "channelData": {"tenant": {"id": "tid"}},
        "serviceUrl": "https://svc/",
        "replyToId": "act-1",
        "value": {
            TEAMS_FORM_CALLBACK_KEY: "conv-1|tool-2",
            "name": "Bob",
            "subscribe": "true",
        },
    }
    interaction = TeamsMessageParser().parse_interaction(payload)
    assert interaction is not None
    assert interaction.callback_id == "conv-1|tool-2"
    assert interaction.values == {"name": "Bob", "subscribe": "true"}
    assert interaction.external_user_id == "29:u"
    assert interaction.dedup_id == "act-9"
    assert interaction.reply_target["conversation_id"] == "19:conv"
    assert interaction.reply_target["reply_to_id"] == "act-1"
    # A plain message (no value dict) is not an interaction.
    assert TeamsMessageParser().parse_interaction({"type": "message"}) is None


# ── Webhook decode + body formatting ─────────────────────────────────────────


def test_decode_webhook_payload_handles_form_encoded_and_json():
    import json
    import urllib.parse

    inner = {"type": "block_actions", "actions": []}
    form_body = urllib.parse.urlencode({"payload": json.dumps(inner)}).encode("utf-8")
    decoded = _decode_webhook_payload(
        form_body, {"content-type": "application/x-www-form-urlencoded"}
    )
    assert decoded == inner

    json_body = json.dumps({"type": "event_callback"}).encode("utf-8")
    assert _decode_webhook_payload(json_body, {"content-type": "application/json"}) == {
        "type": "event_callback"
    }
    assert _decode_webhook_payload(b"", {}) == {}


# ── Telegram native inline keyboards ─────────────────────────────────────────

import pytest
from unittest.mock import AsyncMock, patch

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.adapter import TelegramSurfaceAdapter
from app.modules.agent_surfaces.platforms.telegram.service import _OTHER_CALLBACK_VALUE


def _single_question_plan() -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan(
        title="Which country?",
        callback_id="conv-1|tool-1",
        questions=[
            SurfaceQuestion(
                header="country",
                question="Which country?",
                options=[
                    SurfaceQuestionOption(label="US", recommended=True),
                    SurfaceQuestionOption(label="CA"),
                ],
            )
        ],
    )


def _telegram_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="123",
        message_text="hi",
        is_dm=True,
        reply_target={"chat_id": "123"},
    )


@pytest.mark.asyncio
async def test_telegram_send_questions_builds_inline_keyboard():
    adapter = TelegramSurfaceAdapter()
    tokens = iter(["tok0", "tok1", "tokother"])
    with patch(
        "app.modules.agent_surfaces.platforms.telegram.service.put_callback_token",
        new=AsyncMock(side_effect=lambda payload: next(tokens)),
    ), patch(
        "app.modules.agent_surfaces.platforms.telegram.service."
        "TelegramPlatformService.send_message",
        new=AsyncMock(),
    ) as send_message:
        ok = await adapter.send_questions(
            credentials={"bot_token": "x"},
            event=_telegram_event(),
            question_plan=_single_question_plan(),
        )

    assert ok is True
    keyboard = send_message.await_args.kwargs["metadata"]["reply_markup"][
        "inline_keyboard"
    ]
    # one row per option + a trailing "Other" row; callback_data is the short token
    assert [row[0]["callback_data"] for row in keyboard] == ["tok0", "tok1", "tokother"]
    assert "US" in keyboard[0][0]["text"]
    assert "Other" in keyboard[-1][0]["text"]
    # every callback_data stays within Telegram's 64-byte limit
    assert all(len(row[0]["callback_data"]) <= 64 for row in keyboard)


@pytest.mark.asyncio
async def test_telegram_send_questions_falls_back_on_multi_select():
    adapter = TelegramSurfaceAdapter()
    plan = SurfaceQuestionRenderPlan(
        title="tags",
        callback_id="conv-1|tool-1",
        questions=[
            SurfaceQuestion(
                header="tags",
                question="Which tags?",
                multi_select=True,
                options=[SurfaceQuestionOption(label="a"), SurfaceQuestionOption(label="b")],
            )
        ],
    )
    ok = await adapter.send_questions(
        credentials={"bot_token": "x"},
        event=_telegram_event(),
        question_plan=plan,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_telegram_parse_inbound_interaction_resolves_tap():
    adapter = TelegramSurfaceAdapter()
    payload = {
        "callback_query": {
            "id": "cbq-1",
            "data": "tok0",
            "from": {"id": 555},
            "message": {"chat": {"id": 123}},
        }
    }
    stored = {"callback_id": "conv-1|tool-1", "header": "country", "value": "US"}
    with patch(
        "app.modules.agent_surfaces.platforms.telegram.adapter.get_callback_token",
        new=AsyncMock(return_value=stored),
    ):
        interaction = await adapter.parse_inbound_interaction(payload)

    assert interaction is not None
    assert interaction.callback_id == "conv-1|tool-1"
    assert interaction.values == {"country": "US"}
    assert interaction.external_user_id == "555"
    assert interaction.dedup_id == "cbq-1"


@pytest.mark.asyncio
async def test_telegram_parse_inbound_interaction_other_and_unknown_return_none():
    adapter = TelegramSurfaceAdapter()
    payload = {"callback_query": {"id": "c", "data": "tok", "from": {"id": 1}, "message": {"chat": {"id": 1}}}}
    other = {"callback_id": "conv-1|tool-1", "header": "country", "value": _OTHER_CALLBACK_VALUE}
    with patch(
        "app.modules.agent_surfaces.platforms.telegram.adapter.get_callback_token",
        new=AsyncMock(return_value=other),
    ):
        assert await adapter.parse_inbound_interaction(payload) is None
    with patch(
        "app.modules.agent_surfaces.platforms.telegram.adapter.get_callback_token",
        new=AsyncMock(return_value=None),
    ):
        assert await adapter.parse_inbound_interaction(payload) is None


# ── WhatsApp native interactive replies ──────────────────────────────────────

from app.modules.agent_surfaces.platforms.whatsapp.adapter import WhatsAppSurfaceAdapter


def _whatsapp_event() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.WHATSAPP,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="15551230000",
        message_text="hi",
        is_dm=True,
        sender_phone="15551230000",
        reply_target={"sender_wa_id": "15551230000", "phone_number_id": "pn1"},
    )


@pytest.mark.asyncio
async def test_whatsapp_send_questions_uses_buttons_for_few_options():
    adapter = WhatsAppSurfaceAdapter()
    captured = {}

    async def _fake_post(self, url, json, headers):  # noqa: ANN001
        captured["payload"] = json

        class _Resp:
            def raise_for_status(self):
                return None

        return _Resp()

    with patch("httpx.AsyncClient.post", new=_fake_post):
        ok = await adapter.send_questions(
            credentials={"access_token": "t", "phone_number_id": "pn1"},
            event=_whatsapp_event(),
            question_plan=_single_question_plan(),
        )

    assert ok is True
    interactive = captured["payload"]["interactive"]
    assert interactive["type"] == "button"
    buttons = interactive["action"]["buttons"]
    assert [b["reply"]["title"] for b in buttons] == ["US", "CA"]
    # the id encodes callback_id~header~value and stays within 256 chars
    assert all(len(b["reply"]["id"].encode("utf-8")) <= 256 for b in buttons)
    assert buttons[0]["reply"]["id"] == "conv-1|tool-1~country~US"


@pytest.mark.asyncio
async def test_whatsapp_send_questions_falls_back_on_multi_select():
    adapter = WhatsAppSurfaceAdapter()
    plan = SurfaceQuestionRenderPlan(
        title="tags",
        callback_id="conv-1|tool-1",
        questions=[
            SurfaceQuestion(
                header="tags",
                question="Which tags?",
                multi_select=True,
                options=[SurfaceQuestionOption(label="a"), SurfaceQuestionOption(label="b")],
            )
        ],
    )
    ok = await adapter.send_questions(
        credentials={"access_token": "t", "phone_number_id": "pn1"},
        event=_whatsapp_event(),
        question_plan=plan,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_whatsapp_parse_inbound_interaction_decodes_button_reply():
    adapter = WhatsAppSurfaceAdapter()
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.1",
                                    "from": "15551230000",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": "conv-1|tool-1~country~US",
                                            "title": "US",
                                        },
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    interaction = await adapter.parse_inbound_interaction(payload)
    assert interaction is not None
    assert interaction.callback_id == "conv-1|tool-1"
    assert interaction.values == {"country": "US"}
    assert interaction.external_user_id == "15551230000"
    assert interaction.dedup_id == "wamid.1"


@pytest.mark.asyncio
async def test_whatsapp_parse_inbound_interaction_ignores_non_lemma_id():
    adapter = WhatsAppSurfaceAdapter()
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.2",
                                    "from": "1",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "some-other-id", "title": "x"},
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    assert await adapter.parse_inbound_interaction(payload) is None
