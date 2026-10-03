"""What each kind of WhatsApp message reaches the agent as.

Every case here is a message that used to arrive as something the person never
said: a reaction that started a run, a location read as the word "location", a
form submission printed as a Python dict.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.agent_surfaces.platforms.whatsapp.message_bodies import (
    message_body,
)
from app.modules.agent_surfaces.platforms.whatsapp.parser import (
    WhatsAppMessageParser,
    split_whatsapp_deliveries,
)

SENDER = "15550555555"


def _delivery(
    *messages: dict[str, Any],
    contacts: list[dict[str, Any]] | None = None,
    display_number: str = "15550000000",
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "phone_number_id": "1234567890",
                                "display_phone_number": display_number,
                            },
                            "contacts": contacts
                            if contacts is not None
                            else [{"wa_id": SENDER, "profile": {"name": "Ada"}}],
                            "messages": list(messages),
                        }
                    }
                ],
            }
        ],
    }


def _msg(kind: str, *, sender: str = SENDER, **body: Any) -> dict[str, Any]:
    return {"from": sender, "id": f"wamid-{kind}", "type": kind, **body}


def _parse(payload: dict[str, Any]):
    return WhatsAppMessageParser().parse(payload)


@pytest.mark.parametrize(
    "message",
    [
        _msg("reaction", reaction={"message_id": "wamid-out", "emoji": "👍"}),
        _msg("system", system={"type": "user_changed_number", "wa_id": "1555"}),
        _msg("request_welcome"),
    ],
)
def test_a_notice_about_the_chat_starts_nothing(message):
    """A reaction or a changed number used to start a run -- or a signup."""
    assert _parse(_delivery(message)) is None


def test_a_location_reads_as_a_place_with_a_map_link():
    text, attachments = message_body(
        _msg(
            "location",
            location={
                "latitude": 51.5,
                "longitude": -0.12,
                "name": "Trafalgar Square",
                "address": "London",
            },
        )
    )

    assert text.startswith("📍")
    assert "Trafalgar Square, London" in text
    assert "(51.5,-0.12)" in text
    assert "https://maps.google.com/?q=51.5,-0.12" in text
    assert attachments == []


def test_a_contact_card_reads_as_its_name_and_numbers():
    text, _ = message_body(
        _msg(
            "contacts",
            contacts=[
                {
                    "name": {"formatted_name": "Ada Lovelace"},
                    "phones": [{"phone": "+44 20 7946 0000"}],
                    "emails": [{"email": "ada@example.com"}],
                    "org": {"company": "Analytical Engines"},
                }
            ],
        )
    )

    assert text == (
        "Shared contact:\n- Ada Lovelace — +44 20 7946 0000 — ada@example.com"
        " — Analytical Engines"
    )


def test_a_template_button_reads_as_what_it_said():
    text, _ = message_body(_msg("button", button={"text": "Yes", "payload": "Y"}))
    assert text == "Yes"
    text, _ = message_body(_msg("button", button={"payload": "CONFIRM"}))
    assert text == "CONFIRM"


def test_an_order_reads_as_its_lines():
    text, _ = message_body(
        _msg(
            "order",
            order={
                "text": "for Friday",
                "product_items": [
                    {
                        "product_retailer_id": "SKU-1",
                        "quantity": 2,
                        "item_price": 9.5,
                        "currency": "EUR",
                    }
                ],
            },
        )
    )

    assert (
        text == "Placed an order from the catalog: for Friday\n- 2 × SKU-1 at 9.5 EUR"
    )


def test_an_unknown_type_is_said_to_be_unreadable_not_named():
    text, attachments = message_body(_msg("poll", poll={"question": "?"}))

    assert text.startswith("(System notice, not the person's words:")
    assert "poll" in text
    assert attachments == []


def test_an_expired_form_submission_is_never_printed_as_a_dict():
    text, _ = message_body(
        _msg(
            "interactive",
            interactive={
                "type": "nfm_reply",
                "nfm_reply": {"response_json": '{"flow_token": "t"}', "name": "flow"},
            },
        )
    )

    assert "{" not in text
    assert "no longer open" in text
    assert "System notice" in text


def test_a_text_body_that_is_not_text_is_not_stringified():
    text, _ = message_body(_msg("text", text={"body": {"unexpected": True}}))
    assert text == ""


def test_media_without_an_id_carries_no_attachment():
    """Nothing to download; an attachment that cannot be fetched only fails later."""
    text, attachments = message_body(_msg("image", image={"mime_type": "image/png"}))
    assert text == "image"
    assert attachments == []


def test_a_sticker_is_an_attachment():
    _, attachments = message_body(
        _msg("sticker", sticker={"id": "st-1", "mime_type": "image/webp"})
    )
    assert attachments[0]["id"] == "st-1"


def test_each_message_of_a_batch_is_named_after_its_own_sender():
    """``contacts[0]`` named every message after the first sender."""
    other = "15550666666"
    payload = _delivery(
        _msg("text", text={"body": "one"}),
        _msg("text", sender=other, text={"body": "two"}),
        contacts=[
            {"wa_id": SENDER, "profile": {"name": "Ada"}},
            {"wa_id": other, "profile": {"name": "Grace"}},
        ],
    )

    events = [_parse(part) for part in split_whatsapp_deliveries(payload)]

    assert [e.sender_display_name for e in events] == ["Ada", "Grace"]
    assert events[1].metadata["contacts"] == [
        {"wa_id": other, "profile": {"name": "Grace"}}
    ]


def test_a_direct_message_without_a_number_is_answered_by_user_id():
    message = {"from_user_id": "US.123", "id": "wamid-1", "type": "text"}
    message["text"] = {"body": "hi"}

    event = _parse(_delivery(message, contacts=[]))

    assert event is not None
    assert event.sender_external_user_id == "US.123"
    assert event.sender_phone is None
    assert event.reply_target["sender_wa_id"] == "US.123"


def test_a_quoted_reply_carries_a_reference_to_what_it_quotes():
    quoting_bot = _msg(
        "text",
        text={"body": "this one"},
        context={"from": "15550000000", "id": "wamid-out-7"},
    )
    quoting_self = _msg(
        "text", text={"body": "and this"}, context={"from": SENDER, "id": "wamid-in"}
    )
    forwarded = _msg("text", text={"body": "fwd"}, context={"forwarded": True})

    assert _parse(_delivery(quoting_bot)).metadata["reply_ref"] == {
        "id": "wamid-out-7",
        "from": "15550000000",
        "is_bot": True,
    }
    assert _parse(_delivery(quoting_self)).metadata["reply_ref"]["is_bot"] is False
    assert _parse(_delivery(forwarded)).metadata["reply_ref"] is None
