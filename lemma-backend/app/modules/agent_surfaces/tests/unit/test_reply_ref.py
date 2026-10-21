"""``reply_ref_of``: a heard line keeps how to answer it, nothing it said.

Kept on every heard line, so it must not be a second copy of the message --
its words, the sender's address, number or name, attachments, a subject --
and must still be an event a reply can be built from.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.services.channel_lines import reply_ref_of


def _email() -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="RESEND",
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="support@pod.example.com",
        external_thread_id="<root@example.com>",
        external_message_id="<m2@example.com>",
        sender_external_user_id="dana@example.com",
        sender_email="dana@example.com",
        sender_phone="+15550001111",
        sender_display_name="Dana Doe",
        sender_authentication="pass",
        message_text="My card ending 4242 was charged twice",
        is_dm=True,
        reply_target={
            "recipient_email": "dana@example.com",
            "subject": "Re: double charge",
            "in_reply_to": "<m2@example.com>",
            "references": ["<root@example.com>", "<m2@example.com>"],
        },
        metadata={
            "mailbox_email": "support@pod.example.com",
            "thread_id": "<root@example.com>",
            "subject": "Re: double charge",
            "reply_to_email": "dana@example.com",
            "attachments": [{"name": "statement.pdf"}],
            "email_id": "em_123",
        },
        raw_payload={"text": "My card ending 4242 was charged twice"},
    )


def test_a_reply_ref_holds_no_words_address_number_or_name():
    ref = reply_ref_of(_email())

    assert ref["message_text"] == ""
    for field in (
        "sender_email",
        "sender_phone",
        "sender_display_name",
        "sender_authentication",
    ):
        assert ref[field] is None, field
    assert "raw_payload" not in ref
    assert ref["metadata"] == {
        "mailbox_email": "support@pod.example.com",
        "thread_id": "<root@example.com>",
    }
    assert "4242" not in str(ref)


def test_a_reply_ref_still_answers_the_message_in_its_thread():
    event = _email()
    answered = ParsedInboundSurfaceEvent.model_validate(reply_ref_of(event))

    assert answered.reply_target == event.reply_target
    assert answered.external_thread_id == event.external_thread_id
    assert answered.external_message_id == event.external_message_id
    assert answered.sender_external_user_id == event.sender_external_user_id
    assert answered.is_dm and answered.platform is event.platform
