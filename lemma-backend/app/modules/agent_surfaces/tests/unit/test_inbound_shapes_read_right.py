"""Platform deliveries that are not a person typing text, read for what they are.

Each case is a shape a platform really sends that used to be misread: a reaction
started a model run, a location arrived empty, a reply to another bot woke this
one, an email domain counted as a mention.
"""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.telegram.adapter import TelegramSurfaceAdapter

from copy import deepcopy

import pytest

from app.modules.agent_surfaces.domain.addressing import names_the_agent
from app.modules.agent_surfaces.domain.models import (
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    question_blocks_fit,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.platforms.teams.parser import TeamsMessageParser
from app.modules.agent_surfaces.platforms.telegram.parser import (
    TelegramMessageParser,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
)
from app.modules.agent_surfaces.platforms.telegram.addressing import (
    addressed_to_this_bot,
)


# ── WhatsApp ────────────────────────────────────────────────────────────────


def _whatsapp(message: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pn-1"},
                            "contacts": [
                                {"wa_id": "15550001", "profile": {"name": "Asha"}}
                            ],
                            "messages": [
                                {"from": "15550001", "id": "wamid-1", **message}
                            ],
                        }
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "message",
    [
        {"type": "reaction", "reaction": {"message_id": "wamid-0", "emoji": "👍"}},
        {"type": "system", "system": {"body": "Asha changed their number"}},
    ],
    ids=["reaction", "system"],
)
def test_a_whatsapp_delivery_that_is_not_a_message_starts_nothing(message):
    assert WhatsAppMessageParser().parse(_whatsapp(message)) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {
                "type": "location",
                "location": {
                    "latitude": 12.97,
                    "longitude": 77.59,
                    "name": "Office",
                    "address": "MG Road",
                },
            },
            "Shared a location: 12.97,77.59 (Office, MG Road)",
        ),
        (
            {
                "type": "contacts",
                "contacts": [
                    {
                        "name": {"formatted_name": "Ravi Kumar"},
                        "phones": [{"phone": "+15550002"}],
                    }
                ],
            },
            "Shared contacts: Ravi Kumar (+15550002)",
        ),
        (
            {"type": "button", "button": {"text": "Yes, book it", "payload": "Y"}},
            "Yes, book it",
        ),
    ],
    ids=["location", "contacts", "template-button"],
)
def test_a_whatsapp_message_without_text_still_says_what_was_sent(message, expected):
    event = WhatsAppMessageParser().parse(_whatsapp(message))

    assert event is not None
    assert event.message_text == expected
    assert event.metadata["attachments"] == []


def test_a_whatsapp_type_lemma_cannot_read_is_not_answered_as_its_type_name():
    event = WhatsAppMessageParser().parse(
        _whatsapp({"type": "order", "order": {"catalog_id": "c-1"}})
    )

    assert event is not None
    assert event.message_text != "order"
    assert "cannot read" in event.message_text
    assert event.metadata["attachments"] == []


# ── Telegram ────────────────────────────────────────────────────────────────


def _telegram_group(**message: object) -> dict:
    return {
        "update_id": 7,
        "message": {
            "message_id": 41,
            "from": {"id": 1001, "first_name": "Asha"},
            "chat": {"id": -100555, "type": "supergroup", "title": "Ops"},
            "date": 1700000000,
            **message,
        },
    }


def test_a_reply_to_another_bot_is_not_put_to_this_one():
    parsed = TelegramMessageParser().parse(
        _telegram_group(
            text="thanks, that worked",
            reply_to_message={
                "message_id": 40,
                "from": {"id": 9001, "is_bot": True, "username": "otherbot"},
            },
        )
    )
    assert parsed is not None
    # Settled only once getMe says who we are, so ingress must ask.
    assert TelegramSurfaceAdapter().mention_is_provisional(parsed)

    settled = addressed_to_this_bot(parsed, bot_username="lemmabot", bot_user_id="42")

    assert settled.mentioned_agent is False
    assert settled.metadata["is_thread_reply"] is False


def test_a_reply_to_this_bot_is_still_put_to_it():
    parsed = TelegramMessageParser().parse(
        _telegram_group(
            text="and the other one?",
            reply_to_message={
                "message_id": 40,
                "from": {"id": 42, "is_bot": True, "username": "lemmabot"},
            },
        )
    )
    assert parsed is not None

    settled = addressed_to_this_bot(parsed, bot_username="lemmabot", bot_user_id="42")

    assert settled.mentioned_agent is True
    assert settled.metadata["is_thread_reply"] is True


def test_naming_this_bot_in_a_reply_to_another_still_reaches_it():
    parsed = TelegramMessageParser().parse(
        _telegram_group(
            text="@lemmabot what do you think?",
            entities=[{"type": "mention", "offset": 0, "length": 9}],
            reply_to_message={"message_id": 40, "from": {"id": 9001, "is_bot": True}},
        )
    )
    assert parsed is not None

    settled = addressed_to_this_bot(parsed, bot_username="LemmaBot", bot_user_id="42")

    assert settled.mentioned_agent is True


def test_a_telegram_service_message_with_nothing_in_it_is_not_an_event():
    payload = _telegram_group(pinned_message={"message_id": 40, "text": "old"})

    assert TelegramMessageParser().parse(payload) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            {"location": {"latitude": 12.97, "longitude": 77.59}},
            "Shared a location: 12.97,77.59",
        ),
        (
            {
                "venue": {
                    "location": {"latitude": 1.5, "longitude": 2.5},
                    "title": "Cafe",
                    "address": "1 Main St",
                }
            },
            "Shared a location: 1.5,2.5 (Cafe, 1 Main St)",
        ),
        (
            {
                "contact": {
                    "phone_number": "+15550002",
                    "first_name": "Ravi",
                    "user_id": 777,
                }
            },
            "Shared a contact: Ravi (+15550002)",
        ),
    ],
    ids=["location", "venue", "someone-elses-contact"],
)
def test_a_telegram_place_or_contact_arrives_as_words(message, expected):
    payload = _telegram_group(**message)
    payload["message"]["chat"] = {"id": 1001, "type": "private"}

    parsed = TelegramMessageParser().parse(payload)

    assert parsed is not None
    assert parsed.message_text == expected


# ── Slack ───────────────────────────────────────────────────────────────────


def test_a_thread_reply_also_sent_to_the_channel_is_admitted():
    parsed = SlackMessageParser().parse(
        {
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "message",
                "subtype": "thread_broadcast",
                "user": "U1",
                "text": "following up on this",
                "channel": "C1",
                "channel_type": "channel",
                "ts": "1700000001.000200",
                "thread_ts": "1700000000.000100",
            },
        }
    )

    assert parsed is not None
    assert parsed.external_thread_id == "1700000000.000100"
    assert parsed.message_text == "following up on this"


def _questions(*questions: SurfaceQuestion) -> SurfaceQuestionRenderPlan:
    return SurfaceQuestionRenderPlan(
        title="Questions", callback_id="conv|tool", questions=list(questions)
    )


def _question(header: str, *labels: str) -> SurfaceQuestion:
    return SurfaceQuestion(
        header=header,
        question=f"{header}?",
        options=[SurfaceQuestionOption(label=label) for label in labels],
    )


def test_ordinary_questions_fit_in_block_kit():
    assert question_blocks_fit(_questions(_question("country", "US", "CA")))


@pytest.mark.parametrize(
    "plan",
    [
        _questions(_question("pick", "x" * 200, "short")),
        _questions(_question("same", "a"), _question("same", "b")),
        _questions(_question("dupes", "a", "a")),
        _questions(*(_question(f"q{index}", "a") for index in range(30))),
        _questions(_question("h" * 300, "a")),
    ],
    ids=["label-too-long", "duplicate-headers", "duplicate-labels", "too-many", "id"],
)
def test_questions_slack_would_refuse_are_asked_in_words_instead(plan):
    assert not question_blocks_fit(plan)


# ── Teams ───────────────────────────────────────────────────────────────────


def test_a_legacy_teams_channel_mention_is_detected_from_the_at_tag():
    message = {
        "id": "m-1",
        "text": "<at>Lemma</at> can you summarise?",
        "from": {"user": {"id": "u-1", "displayName": "Asha"}},
        "conversation": {"id": "19:chan@thread.tacv2"},
        "channelData": {"channel": {"id": "19:chan@thread.tacv2"}},
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
    }

    parsed = TeamsMessageParser().parse({"value": [deepcopy(message)]})

    assert parsed is not None
    assert parsed.mentioned_agent is True


# ── Addressing by name ──────────────────────────────────────────────────────


def test_an_email_address_does_not_speak_to_an_agent_named_like_its_domain():
    assert not names_the_agent("please email ops@acme.com for that", "Acme")
    assert names_the_agent("@Acme can you check?", "Acme")
    assert names_the_agent("thanks (@acme)", "Acme")
