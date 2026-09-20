"""What Telegram POSTs to `/surfaces/webhooks/telegram`.

A Telegram tap is the one interaction that cannot be written down. Its
`callback_data` is an opaque token the agent minted and stored in Redis, and
`_interaction_from_token` reads the meaning back out of the store — so a test
that invents one gets `expired`, which is exactly the case pinned below. Real
taps go through `control_value`, which takes the token out of the
`reply_markup` the fake Telegram received.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    SurfacePlatform,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    InboundCase,
    InteractionCase,
    register_inbound,
    register_interactions,
)

SENDER_ID = 5550001
USERNAME = "surfaceuser"


def _from(sender_id: int = SENDER_ID) -> dict[str, Any]:
    return {
        "id": sender_id,
        "is_bot": False,
        "first_name": "Surface",
        "last_name": "User",
        "username": USERNAME,
    }


def dm(
    *,
    text: str = "Hello from Telegram",
    message_id: int = 1,
    sender_id: int = SENDER_ID,
) -> dict[str, Any]:
    return {
        "update_id": message_id + 100000,
        "message": {
            "message_id": message_id,
            "from": _from(sender_id),
            "chat": {"id": sender_id, "type": "private"},
            "date": 1700000000,
            "text": text,
        },
    }


def group_mention(
    *,
    text: str = "what is on my plate?",
    bot_username: str = "lemma_bot",
    message_id: int = 2,
    chat_id: int = -1001234567890,
    sender_id: int = SENDER_ID,
) -> dict[str, Any]:
    mention = f"@{bot_username}"
    return {
        "update_id": message_id + 100000,
        "message": {
            "message_id": message_id,
            "from": _from(sender_id),
            "chat": {"id": chat_id, "type": "supergroup", "title": "Surface Group"},
            "date": 1700000000,
            "text": f"{mention} {text}",
            "entities": [
                {"offset": 0, "length": len(mention), "type": "mention"},
            ],
        },
    }


def contact_share(
    *,
    phone: str = "15550001111",
    message_id: int = 3,
    sender_id: int = SENDER_ID,
    own_contact: bool = True,
) -> dict[str, Any]:
    """The `request_contact` reply. `own_contact=False` is somebody else's card.

    The difference is one field: Telegram fills `contact.user_id` with the
    Telegram account the card belongs to, and only a card whose `user_id` is
    the sender's own proves the sender's number.
    """
    payload = dm(text="", message_id=message_id, sender_id=sender_id)
    payload["message"].pop("text")
    payload["message"]["contact"] = {
        "phone_number": phone,
        "first_name": "Surface",
        "user_id": sender_id if own_contact else sender_id + 1,
    }
    return payload


def photo(
    *,
    message_id: int = 4,
    sender_id: int = SENDER_ID,
    file_id: str = "AgACAgEAAxkBAAIB",
    caption: str = "what is this?",
) -> dict[str, Any]:
    payload = dm(text="", message_id=message_id, sender_id=sender_id)
    payload["message"].pop("text")
    payload["message"]["caption"] = caption
    payload["message"]["photo"] = [
        {
            "file_id": file_id,
            "file_unique_id": "AQADAgAD",
            "width": 1280,
            "height": 720,
            "file_size": 51200,
        }
    ]
    return payload


def voice_note(
    *,
    message_id: int = 5,
    sender_id: int = SENDER_ID,
    file_id: str = "AwACAgEAAxkBAAIB",
) -> dict[str, Any]:
    payload = dm(text="", message_id=message_id, sender_id=sender_id)
    payload["message"].pop("text")
    payload["message"]["voice"] = {
        "file_id": file_id,
        "file_unique_id": "AQADAwAD",
        "duration": 3,
        "mime_type": "audio/ogg",
        "file_size": 4096,
    }
    return payload


def button_press(
    *,
    token: str,
    message_id: int = 902,
    sender_id: int = SENDER_ID,
    callback_query_id: str = "cbq-1",
    update_id: int = 100701,
) -> dict[str, Any]:
    """A tapped inline-keyboard button. `token` is the stored callback data."""
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_query_id,
            "from": _from(sender_id),
            "message": {
                "message_id": message_id,
                "chat": {"id": sender_id, "type": "private"},
                "date": 1700000200,
                "text": "Show a widget",
            },
            "chat_instance": "1234567890123456789",
            "data": token,
        },
    }


@register_inbound(SurfacePlatform.TELEGRAM)
def inbound_cases() -> list[InboundCase]:
    return [
        InboundCase(
            name="dm",
            platform=SurfacePlatform.TELEGRAM,
            payload=dm(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_DM,
                "external_channel_id": str(SENDER_ID),
                "sender_external_user_id": str(SENDER_ID),
                "message_text": "Hello from Telegram",
                "is_dm": True,
            },
        ),
        InboundCase(
            name="group_mention",
            platform=SurfacePlatform.TELEGRAM,
            payload=group_mention(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_GROUP,
                "sender_external_user_id": str(SENDER_ID),
                "is_dm": False,
            },
        ),
        InboundCase(
            name="own_contact_proves_the_number",
            platform=SurfacePlatform.TELEGRAM,
            payload=contact_share(),
            expected={
                "sender_phone": "15550001111",
                "metadata": {
                    "contact_shared": True,
                    "contact_shared_by_sender": True,
                    "shared_contact_phone": "15550001111",
                },
            },
        ),
        InboundCase(
            name="another_persons_contact_proves_nothing",
            platform=SurfacePlatform.TELEGRAM,
            payload=contact_share(own_contact=False),
            expected={
                "sender_phone": None,
                "metadata": {
                    "contact_shared": True,
                    "contact_shared_by_sender": False,
                    "shared_contact_phone": "15550001111",
                },
            },
        ),
        InboundCase(
            name="photo",
            platform=SurfacePlatform.TELEGRAM,
            payload=photo(),
            expected={"is_dm": True, "sender_external_user_id": str(SENDER_ID)},
        ),
        InboundCase(
            name="voice_note",
            platform=SurfacePlatform.TELEGRAM,
            payload=voice_note(),
            expected={"is_dm": True, "sender_external_user_id": str(SENDER_ID)},
        ),
    ]


@register_interactions(SurfacePlatform.TELEGRAM)
def interaction_cases() -> list[InteractionCase]:
    return [
        InteractionCase(
            name="unstored_token_expires_rather_than_disappearing",
            platform=SurfacePlatform.TELEGRAM,
            payload=button_press(token="token-nobody-stored"),
            expected={
                "interaction_state": "expired",
                "external_user_id": str(SENDER_ID),
            },
        ),
    ]
