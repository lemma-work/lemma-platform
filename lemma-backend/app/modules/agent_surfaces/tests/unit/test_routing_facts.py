"""The typed reading of an event's admission facts and reply target."""

from __future__ import annotations

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.reply_targets import (
    EmailReplyTarget,
    SlackReplyTarget,
    WhatsAppReplyTarget,
    reply_target_of,
)
from app.modules.agent_surfaces.domain.routing_facts import InboundRoutingFacts


def _event(**overrides: object) -> ParsedInboundSurfaceEvent:
    fields: dict[str, object] = {
        "platform": "SLACK",
        "conversation_type": ConversationType.EXTERNAL_GROUP,
        "external_thread_id": "1.0",
        "message_text": "hi",
    }
    fields.update(overrides)
    return ParsedInboundSurfaceEvent.model_validate(fields)


def test_admission_facts_are_read_once_and_typed():
    facts = InboundRoutingFacts.of(
        _event(
            metadata={
                "is_thread_reply": True,
                "event_type": "message",
                "mentioned_user_ids": ["U1", 2],
                "reply_to_user_id": 42,
            }
        )
    )
    assert facts.is_thread_reply is True
    assert facts.event_type == "message"
    assert facts.mentioned_user_ids == frozenset({"U1", "2"})
    assert facts.reply_to_user_id == "42"
    assert facts.has_bot_command is False


def test_an_event_without_facts_reads_as_none_of_them():
    facts = InboundRoutingFacts.of(_event())
    assert facts == InboundRoutingFacts()


def test_a_reply_target_is_read_as_its_platforms_model():
    whatsapp = reply_target_of(
        _event(platform="WHATSAPP", reply_target={"phone_number_id": "pn-1"})
    )
    assert isinstance(whatsapp, WhatsAppReplyTarget)
    assert whatsapp.phone_number_id == "pn-1"

    slack = reply_target_of(_event(reply_target={"channel": "C1", "thread_ts": 1.5}))
    assert slack == SlackReplyTarget(channel="C1", thread_ts="1.5")


def test_an_email_reply_target_keeps_its_lists_and_subject_as_written():
    email = reply_target_of(
        _event(
            platform="RESEND",
            reply_target={
                "recipient_email": "a@example.com",
                "subject": " Re: plans ",
                "references": ["<m1>", "<m2>"],
                "cc": ["b@example.com"],
            },
        )
    )
    assert isinstance(email, EmailReplyTarget)
    assert email.subject == " Re: plans "
    assert email.references == ("<m1>", "<m2>")
    assert email.cc == ("b@example.com",)
