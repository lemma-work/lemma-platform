"""A WhatsApp group the bot created: read, addressed and answered as a group.

Meta's Groups API differs from a one-to-one chat in three ways that each used
to be a silent wrong answer here. A group message carries ``group_id`` and was
read as a private message from its sender -- so the reply went to that person's
own number. A group takes no interactive messages, so buttons and cards were
refused. And creation is asynchronous, confirmed by a webhook nothing read.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.addressing import mentions_number
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.groups import GroupUpdateKind
from app.modules.agent_surfaces.domain.models import (
    SurfaceQuestion,
    SurfaceQuestionOption,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    WhatsAppApiError,
    WhatsAppClient,
    groups_api_base,
)
from app.modules.agent_surfaces.platforms.whatsapp.group_updates import (
    whatsapp_group_updates,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import WhatsAppMessageParser
from app.modules.agent_surfaces.platforms.whatsapp.payloads import whatsapp_recipient
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)

GROUP = "HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA"
BUSINESS_NUMBER = "15550783881"


def _group_message(
    text: str, *, sender: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    """Meta's documented group message, word for word where it gives one."""
    message: dict[str, Any] = {
        "from": "16505551234",
        "group_id": GROUP,
        "id": "wamid.group-1",
        "timestamp": "1744344496",
        "text": {"body": text},
        "type": "text",
        **extra,
    }
    if sender is not None:
        message.update(sender)
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": BUSINESS_NUMBER,
                                "phone_number_id": "106540352242922",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Tiago Mingo"},
                                    "wa_id": "16505551234",
                                }
                            ],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _lifecycle(*groups: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "group_lifecycle_update",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": BUSINESS_NUMBER,
                                "phone_number_id": "106540352242922",
                            },
                            "groups": list(groups),
                        },
                    }
                ],
            }
        ],
    }


# --- reading a group message ------------------------------------------------


def test_a_group_message_is_the_group_not_the_sender():
    parsed = WhatsAppMessageParser().parse(_group_message("What does everyone think?"))

    assert parsed is not None
    assert parsed.is_dm is False
    assert parsed.external_channel_id == GROUP
    assert parsed.external_thread_id == GROUP
    assert parsed.sender_external_user_id == "16505551234"
    assert parsed.sender_display_name == "Tiago Mingo"
    # The reply goes to the group; the sender's own number is nowhere in it.
    assert parsed.reply_target == {
        "phone_number_id": "106540352242922",
        "group_id": GROUP,
    }


def test_talk_among_the_group_is_not_put_to_the_bot():
    parsed = WhatsAppMessageParser().parse(_group_message("Thanks all, see you at 3"))

    assert parsed is not None
    assert parsed.mentioned_agent is False
    assert parsed.should_start_conversation is False


def test_an_at_mention_of_the_business_number_is():
    parsed = WhatsAppMessageParser().parse(
        _group_message(f"@{BUSINESS_NUMBER} can you share the price list?")
    )

    assert parsed is not None
    assert parsed.mentioned_agent is True


def test_a_reply_to_the_bots_own_message_is():
    parsed = WhatsAppMessageParser().parse(
        _group_message(
            "And for 20 units?",
            context={"from": BUSINESS_NUMBER, "id": "wamid.bot-answer"},
        )
    )

    assert parsed is not None
    assert parsed.mentioned_agent is True


def test_a_participant_known_only_by_their_business_scoped_id():
    """Meta may withhold the number; `from_user_id` is then all there is."""
    payload = _group_message("hello")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    del message["from"]
    message["from_user_id"] = "US.13491208655302741918"

    parsed = WhatsAppMessageParser().parse(payload)

    assert parsed is not None
    assert parsed.sender_external_user_id == "US.13491208655302741918"
    assert parsed.sender_phone is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("@15550783881 hi", True),
        ("@+15550783881, hi", True),
        ("call 15550783881", False),
        ("@155507838810 is someone else", False),
    ],
)
def test_an_at_mention_is_the_whole_number(text, expected):
    assert mentions_number(text, "+1 555-078-3881") is expected


# --- sending to a group -------------------------------------------------------


def test_a_group_reply_never_falls_back_to_the_senders_number():
    recipient = whatsapp_recipient(
        {"group_id": GROUP, "sender_wa_id": "16505551234"},
        fallback_wa_id="16505551234",
    )

    assert recipient is not None
    assert (recipient.to, recipient.recipient_type) == (GROUP, "group")


def test_a_private_reply_still_goes_to_the_person():
    recipient = whatsapp_recipient({}, fallback_wa_id="16505551234")

    assert recipient is not None
    assert (recipient.to, recipient.recipient_type) == ("16505551234", "individual")


def test_groups_go_through_the_version_that_has_them():
    assert (
        groups_api_base("https://graph.facebook.com/v21.0")
        == "https://graph.facebook.com/v23.0"
    )
    # A test server or proxy names no version, and is left alone.
    assert groups_api_base("http://127.0.0.1:9999/") == "http://127.0.0.1:9999"


def test_metas_own_error_code_is_read_off_the_body():
    """How a number Meta keeps out of groups is told from any other refusal."""
    refused = WhatsAppApiError(
        method="groups.create",
        status_code=400,
        body_excerpt=(
            '{"error":{"message":"(#131215) This phone number is not eligible '
            'to access Groups APIs","code":131215,"type":"OAuthException"}}'
        ),
    )
    assert refused.meta_code == 131215
    assert WhatsAppApiError(method="groups.create", status_code=502).meta_code is None


class _RecordingClient(WhatsAppClient):
    """The real client with its one network call replaced by a record of it."""

    def __init__(self) -> None:
        super().__init__(access_token="token", phone_number_id="106540352242922")
        self.sent: list[dict] = []

    async def send_message_payload(
        self, *, phone_number_id: str, payload: dict[str, Any]
    ) -> str | None:
        del phone_number_id
        self.sent.append(payload)
        return "wamid.out"


def _service_capturing() -> tuple[WhatsAppPlatformService, list[dict]]:
    client = _RecordingClient()
    service = WhatsAppPlatformService(
        {"access_token": "token", "phone_number_id": "106540352242922"},
        client=client,
    )
    return service, client.sent


def _parsed_group_event():
    parsed = WhatsAppMessageParser().parse(_group_message(f"@{BUSINESS_NUMBER} hi"))
    assert parsed is not None
    return parsed


async def test_the_answer_is_sent_to_the_group():
    service, sent = _service_capturing()

    await service.send_message(_parsed_group_event(), "Here is the price list.")

    assert [(p["recipient_type"], p["to"]) for p in sent] == [("group", GROUP)]


async def test_a_question_in_a_group_is_asked_in_words():
    """No buttons in a group: declining hands the question to the text fallback."""
    service, sent = _service_capturing()
    plan = SurfaceQuestionRenderPlan(
        title="Order",
        callback_id="cb-1",
        questions=[
            SurfaceQuestion(
                header="Size",
                question="Which size?",
                options=[
                    SurfaceQuestionOption(label="Small"),
                    SurfaceQuestionOption(label="Large"),
                ],
            )
        ],
    )

    assert await service._render_choices(_parsed_group_event(), plan) is False
    assert sent == []


async def test_nothing_is_marked_read_in_a_group():
    """Meta documents no read receipt, typing or reaction from a business there."""
    service, sent = _service_capturing()

    await service.add_processing_indicator(_parsed_group_event())

    assert sent == []


# --- the group itself ----------------------------------------------------------


def test_a_confirmed_creation_names_its_request_and_group():
    updates = whatsapp_group_updates(
        _lifecycle(
            {
                "timestamp": "1744344496",
                "group_id": GROUP,
                "type": "group_create",
                "request_id": "req-1",
                "subject": "Acme x Northwind",
                "invite_link": "https://chat.whatsapp.com/LINK",
                "join_approval_mode": "auto_approve",
            }
        )
    )

    assert [(u.kind, u.request_id, u.external_channel_id) for u in updates] == [
        (GroupUpdateKind.CREATED, "req-1", GROUP)
    ]
    assert updates[0].title == "Acme x Northwind"
    assert updates[0].invite_link == "https://chat.whatsapp.com/LINK"
    assert updates[0].phone_number_id == "106540352242922"


def test_a_refused_creation_is_one_with_errors():
    """Meta sends no status flag; the errors array is the only sign."""
    updates = whatsapp_group_updates(
        _lifecycle(
            {
                "timestamp": 1744344496,
                "type": "group_create",
                "request_id": "req-2",
                "errors": [{"code": 131215, "title": "Not eligible"}],
            }
        )
    )

    assert [(u.kind, u.request_id) for u in updates] == [
        (GroupUpdateKind.CREATE_FAILED, "req-2")
    ]


def test_a_deletion_and_nothing_else():
    updates = whatsapp_group_updates(
        _lifecycle(
            {"timestamp": "1", "group_id": GROUP, "type": "group_delete"},
            {
                "timestamp": "1",
                "group_id": "other",
                "type": "group_delete",
                "errors": [{}],
            },
            {"timestamp": "1", "group_id": GROUP, "type": "group_participants_add"},
        )
    )

    assert [(u.kind, u.external_channel_id) for u in updates] == [
        (GroupUpdateKind.DELETED, GROUP)
    ]


def test_a_message_webhook_carries_no_group_update():
    assert whatsapp_group_updates(_group_message("hello")) == []


# --- being in a group is the authorization -----------------------------------


def _whatsapp_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="whatsapp",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.WHATSAPP,
        account_id=None,
        config=SurfaceConfig(),
        is_active=True,
    )


def test_an_addressed_group_message_needs_no_channel_route():
    """The bot is only ever in groups it created; ingress narrowed to the creator."""
    assert _whatsapp_surface().allows_inbound_event(_parsed_group_event()) is True


def test_an_unaddressed_one_is_still_not_answered():
    parsed = WhatsAppMessageParser().parse(_group_message("Thanks all"))
    assert parsed is not None

    assert _whatsapp_surface().allows_inbound_event(parsed) is False


# --- what a group's conversations are called ----------------------------------


def test_a_group_nobody_routed_is_named_by_what_the_platform_sent():
    """Telegram sends a group's title with every message; that is its name."""
    from app.modules.agent_surfaces.domain.channel_names import (
        configured_channel_name,
    )
    from app.modules.agent_surfaces.platforms.telegram.parser import (
        TelegramMessageParser,
    )

    parsed = TelegramMessageParser().parse(
        {
            "update_id": 1,
            "message": {
                "message_id": 7,
                "from": {"id": 42, "is_bot": False, "first_name": "Mara"},
                "chat": {"id": -100123, "type": "supergroup", "title": "Launch crew"},
                "date": 1700000000,
                "text": "hello",
            },
        }
    )

    assert parsed is not None
    assert configured_channel_name(_whatsapp_surface(), parsed) == "Launch crew"


def test_a_whatsapp_group_is_named_from_the_pods_record_of_it():
    """Meta's message webhook names no group; the pod's record does."""
    from app.modules.agent_surfaces.domain.channel_names import (
        configured_channel_name,
    )
    from app.modules.agent_surfaces.domain.groups import SurfaceGroup
    from app.modules.agent_surfaces.services.outsiders import named_after

    group = SurfaceGroup(
        pod_id=uuid4(),
        surface_id=uuid4(),
        platform="WHATSAPP",
        external_channel_id=GROUP,
        title="Acme × Northwind",
    )

    parsed = named_after(_parsed_group_event(), group)

    assert configured_channel_name(_whatsapp_surface(), parsed) == "Acme × Northwind"
