"""Mail a machine wrote is never answered as a contact's: that is how bots loop."""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.platforms.email_automated import automated_reason
from app.modules.agent_surfaces.platforms.resend.parser import (
    ResendInboundParser,
    merge_received_email,
)

pytestmark = pytest.mark.unit

PERSON = "dana@client.example"


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"auto-submitted": "auto-replied"}, "auto_submitted"),
        ({"auto-submitted": "auto-generated"}, "auto_submitted"),
        ({"precedence": "bulk"}, "bulk"),
        ({"precedence": "Junk"}, "bulk"),
        ({"precedence": "list"}, "bulk"),
        ({"list-id": "<news.client.example>"}, "mailing_list"),
        ({"list-unsubscribe": "<mailto:leave@client.example>"}, "mailing_list"),
        ({"x-autoreply": "yes"}, "auto_reply"),
    ],
)
def test_headers_that_say_a_machine_sent_it(headers, reason):
    assert automated_reason(headers, PERSON) == reason


@pytest.mark.parametrize(
    "sender",
    [
        "MAILER-DAEMON@mx.client.example",
        "postmaster@client.example",
        "noreply@client.example",
        "no-reply@client.example",
        "noreply+orders@client.example",
        "do-not-reply@client.example",
    ],
)
def test_addresses_nobody_reads(sender):
    assert automated_reason({}, sender) == "machine_sender"


@pytest.mark.parametrize(
    "headers",
    [{}, {"auto-submitted": "no"}, {"precedence": "first-class"}],
)
def test_a_person_writing_is_not_a_machine(headers):
    assert automated_reason(headers, PERSON) is None
    assert automated_reason({}, "noreen@client.example") is None


def test_the_parser_marks_an_auto_reply_once_the_headers_are_fetched():
    event = ResendInboundParser().parse(
        {
            "from": PERSON,
            "to": "help@ops.lemma.work",
            "subject": "Out of office",
            "text": "I am away until Monday.",
            "message_id": "<ooo-1@client.example>",
        }
    )
    assert event is not None
    assert "automated" not in event.metadata

    merged = merge_received_email(
        event,
        {
            "text": "I am away until Monday.",
            "headers": {"From": PERSON, "Auto-Submitted": "auto-replied"},
        },
    )

    assert merged is not None
    assert merged.metadata["automated"] == "auto_submitted"


def test_the_parser_marks_a_delivery_report_from_the_sender_alone():
    event = ResendInboundParser().parse(
        {
            "from": "MAILER-DAEMON@mx.client.example",
            "to": "help@ops.lemma.work",
            "subject": "Undelivered Mail Returned to Sender",
            "text": "This is the mail system.",
            "message_id": "<bounce-1@client.example>",
        }
    )

    assert event is not None
    assert event.metadata["automated"] == "machine_sender"
