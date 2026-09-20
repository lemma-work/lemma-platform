"""What Meta POSTs to `/surfaces/webhooks/whatsapp`.

One delivery can carry several messages: `entry[].changes[].value.messages[]`
is a list, and the ingress path splits it. The envelope is written once here
and the cases vary the message inside it, because a builder that rebuilds three
levels of nesting per case is a builder that gets one of them wrong.
"""

from __future__ import annotations

import json
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

SENDER_PHONE = "15550777777"
PHONE_NUMBER_ID = "1234567890"
WABA_ID = "waba-001"

#: `callback_id~header~value`, and the header reserved for an approval tap.
INTERACTION_SEP = "~"
APPROVAL_HEADER = "__approval__"


def envelope(
    *messages: dict[str, Any],
    phone_number_id: str = PHONE_NUMBER_ID,
    waba_id: str = WABA_ID,
    sender_phone: str = SENDER_PHONE,
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "contacts": [
                                {
                                    "wa_id": sender_phone,
                                    "profile": {"name": "Surface Test User"},
                                }
                            ],
                            "messages": list(messages),
                        }
                    }
                ],
            }
        ],
    }


def _message(
    message_id: str, *, sender_phone: str = SENDER_PHONE, **body: Any
) -> dict[str, Any]:
    return {
        "from": sender_phone,
        "id": message_id,
        "timestamp": "1700000000",
        **body,
    }


def text(
    *,
    body: str = "Hello from WhatsApp",
    message_id: str = "wamid-text-001",
    sender_phone: str = SENDER_PHONE,
) -> dict[str, Any]:
    return envelope(
        _message(
            message_id, sender_phone=sender_phone, type="text", text={"body": body}
        ),
        sender_phone=sender_phone,
    )


def document(
    *,
    caption: str = "Please check this invoice",
    message_id: str = "wamid-doc-001",
    media_id: str = "onboarding-invoice",
    filename: str = "invoice.txt",
    mime_type: str = "text/plain",
) -> dict[str, Any]:
    return envelope(
        _message(
            message_id,
            type="document",
            document={
                "id": media_id,
                "filename": filename,
                "mime_type": mime_type,
                "caption": caption,
            },
        )
    )


def voice_note(
    *, message_id: str = "wamid-audio-001", media_id: str = "voice-001"
) -> dict[str, Any]:
    return envelope(
        _message(
            message_id,
            type="audio",
            audio={
                "id": media_id,
                "mime_type": "audio/ogg; codecs=opus",
                "voice": True,
            },
        )
    )


def button_press(
    *,
    reply_id: str,
    title: str = "Approve",
    message_id: str = "wamid-interactive-001",
) -> dict[str, Any]:
    """A tapped interactive button. `reply_id` is `callback_id~header~value`."""
    return envelope(
        _message(
            message_id,
            type="interactive",
            interactive={
                "type": "button_reply",
                "button_reply": {"id": reply_id, "title": title},
            },
        )
    )


def flow_reply(
    *, token: str, answer: str, message_id: str = "wamid-flow-001"
) -> dict[str, Any]:
    """A published Flow's reply, which arrives as JSON inside a string."""
    return envelope(
        _message(
            message_id,
            type="interactive",
            interactive={
                "type": "nfm_reply",
                "nfm_reply": {
                    "response_json": json.dumps({"flow_token": token, "answer": answer})
                },
            },
        )
    )


def batched(*bodies: str) -> dict[str, Any]:
    """One delivery carrying several messages, which the ingress path splits."""
    return envelope(
        *(
            _message(f"wamid-batch-{index}", type="text", text={"body": body})
            for index, body in enumerate(bodies)
        )
    )


@register_inbound(SurfacePlatform.WHATSAPP)
def inbound_cases() -> list[InboundCase]:
    return [
        InboundCase(
            name="text",
            platform=SurfacePlatform.WHATSAPP,
            payload=text(),
            expected={
                "conversation_type": ConversationType.EXTERNAL_DM,
                "sender_external_user_id": SENDER_PHONE,
                "sender_phone": SENDER_PHONE,
                "message_text": "Hello from WhatsApp",
                "external_message_id": "wamid-text-001",
                "is_dm": True,
            },
        ),
        InboundCase(
            name="document",
            platform=SurfacePlatform.WHATSAPP,
            payload=document(),
            expected={
                "sender_external_user_id": SENDER_PHONE,
                "external_message_id": "wamid-doc-001",
            },
        ),
        InboundCase(
            name="voice_note",
            platform=SurfacePlatform.WHATSAPP,
            payload=voice_note(),
            expected={"sender_external_user_id": SENDER_PHONE},
        ),
    ]


@register_interactions(SurfacePlatform.WHATSAPP)
def interaction_cases() -> list[InteractionCase]:
    callback = "11111111-1111-4111-8111-111111111111|call_abc"
    return [
        InteractionCase(
            name="approval_button",
            platform=SurfacePlatform.WHATSAPP,
            payload=button_press(
                reply_id=INTERACTION_SEP.join(
                    (callback, APPROVAL_HEADER, "APPROVE_ONCE")
                )
            ),
            expected={
                "callback_id": callback,
                "approval_decision": "APPROVE_ONCE",
                "external_user_id": SENDER_PHONE,
            },
        ),
        InteractionCase(
            name="answer_button",
            platform=SurfacePlatform.WHATSAPP,
            payload=button_press(
                reply_id=INTERACTION_SEP.join((callback, "choice", "blue")),
                title="Blue",
            ),
            expected={"callback_id": callback, "values": {"choice": "blue"}},
        ),
        InteractionCase(
            name="a_non_lemma_interactive_is_left_to_the_message_path",
            platform=SurfacePlatform.WHATSAPP,
            payload=button_press(reply_id="somebody-elses-button"),
            expected={},
            refused=True,
        ),
    ]
