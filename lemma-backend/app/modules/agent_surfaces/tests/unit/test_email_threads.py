"""An email thread with other people on it is a group, and is answered like one.

Two behaviours, both of which used to be wrong in the same thread: the pod
answered every message it was copied on, and it answered only the sender,
dropping everybody else off the thread.
"""

from __future__ import annotations

import pytest

from app.modules.agent_surfaces.domain.addressing import names_the_agent
from app.modules.agent_surfaces.platforms.resend.email_recipients import (
    MAX_REPLY_CC,
    other_people,
    pod_was_addressed,
)
from app.modules.agent_surfaces.platforms.resend.parser import (
    ResendInboundParser,
    merge_received_email,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    to_sender_alone,
)

pytestmark = pytest.mark.unit

POD = "kit.acme@mail.lemma.work"


def _payload(
    *,
    to: list[str],
    cc: list[str] | None = None,
    text: str = "Can you resend our invoice?",
) -> dict:
    return {
        "from": "Client <client@northwind.test>",
        "to": POD,
        "recipients": [POD],
        "addressed_to": to,
        "cc": cc or [],
        "subject": "Invoice",
        "text": text,
        "message_id": "<m1@northwind.test>",
    }


def test_the_reply_goes_to_everybody_else_on_the_thread():
    parsed = ResendInboundParser().parse(
        _payload(to=[POD, "arjun@acme.test"], cc=["priya@acme.test"])
    )

    assert parsed is not None
    assert parsed.reply_target["recipient_email"] == "client@northwind.test"
    assert parsed.reply_target["cc"] == ["arjun@acme.test", "priya@acme.test"]


def test_in_to_is_asked():
    parsed = ResendInboundParser().parse(_payload(to=[POD], cc=["priya@acme.test"]))

    assert parsed is not None
    assert parsed.should_start_conversation is True


def test_only_copied_is_not_asked():
    parsed = ResendInboundParser().parse(_payload(to=["arjun@acme.test"], cc=[POD]))

    assert parsed is not None
    assert parsed.should_start_conversation is False


def test_a_payload_that_never_said_who_was_addressed_is_still_answered():
    """Older and replayed payloads carry no To list; silence would be new."""
    assert pod_was_addressed(addressed_to=[], cc=[], own_address=POD)


@pytest.mark.parametrize(
    ("to", "cc"),
    [
        # Forwarded from a shared inbox, or through an alias: the pod's address
        # is only on the envelope.
        (["support@acme.test"], []),
        # Bcc'd: nobody else can see it was sent to the pod at all.
        (["arjun@acme.test"], ["priya@acme.test"]),
    ],
)
def test_mail_that_reached_the_pod_without_copying_it_is_asked(to, cc):
    parsed = ResendInboundParser().parse(_payload(to=to, cc=cc))

    assert parsed is not None
    assert parsed.should_start_conversation is True


def test_the_sender_the_pod_and_repeats_are_never_copied():
    people = other_people(
        addressed_to=[POD, "Arjun <ARJUN@acme.test>"],
        cc=["arjun@acme.test", "client@northwind.test", "priya@acme.test"],
        sender="client@northwind.test",
        own_address=POD,
    )

    assert people == ["ARJUN@acme.test", "priya@acme.test"]


def test_a_mailing_list_is_not_copied_wholesale():
    people = other_people(
        addressed_to=[POD],
        cc=[f"person{n}@acme.test" for n in range(40)],
        sender="client@northwind.test",
        own_address=POD,
    )

    assert len(people) == MAX_REPLY_CC


@pytest.mark.parametrize(
    "text",
    [
        "Kit, send them the invoice",
        "Thanks all.\n\nKit can you send it?",
        "@kit please",
    ],
)
def test_a_line_that_speaks_to_the_agent_by_name_asks_it(text):
    assert names_the_agent(text, "Kit")


@pytest.mark.parametrize(
    "text",
    [
        "Thanks, received.",
        "The press kit is ready",
        "Lemma is great",
        # A name at the start of a line, talked about rather than to.
        "Kit numbers are up this week",
    ],
)
def test_mentioning_the_word_in_passing_does_not(text):
    assert not names_the_agent(text, "Kit")
    assert not names_the_agent(text, "Lem")


def test_the_fetched_email_decides_who_else_is_on_the_thread():
    """The webhook may carry less than the Received Emails API does."""
    parsed = ResendInboundParser().parse(_payload(to=[POD]))
    assert parsed is not None

    merged = merge_received_email(
        parsed,
        {
            "text": "Can you resend our invoice?",
            "to": ["arjun@acme.test"],
            "cc": [POD, "priya@acme.test"],
            "headers": {},
        },
    )

    assert merged is not None
    assert merged.reply_target["cc"] == ["arjun@acme.test", "priya@acme.test"]
    assert merged.should_start_conversation is False


@pytest.mark.parametrize(
    "text", ["Thanks!", "Got it, thanks so much", "Thanks!\n--\nTiago Mingo\nNorthwind"]
)
def test_thanks_on_a_thread_with_others_on_it_starts_nothing(text):
    """Answering it would copy everybody on the thread with "you're welcome"."""
    parsed = ResendInboundParser().parse(
        _payload(to=[POD], cc=["priya@acme.test"], text=text)
    )

    assert parsed is not None
    assert parsed.should_start_conversation is False


def test_thanks_that_also_asks_is_still_asked():
    parsed = ResendInboundParser().parse(
        _payload(
            to=[POD],
            cc=["priya@acme.test"],
            text="Thanks! Can you also send the purchase order?",
        )
    )

    assert parsed is not None
    assert parsed.should_start_conversation is True


def test_thanks_between_the_sender_and_the_pod_alone_is_still_answered():
    parsed = ResendInboundParser().parse(_payload(to=[POD], text="Thanks!"))

    assert parsed is not None
    assert parsed.should_start_conversation is True


def test_a_refusal_goes_to_the_sender_alone():
    """Never "you don't have access" copied to a client on the thread."""
    parsed = ResendInboundParser().parse(
        _payload(to=[POD, "arjun@acme.test"], cc=["priya@acme.test"])
    )
    assert parsed is not None

    refused = to_sender_alone(parsed)

    assert refused.reply_target["cc"] == []
    assert refused.reply_target["recipient_email"] == "client@northwind.test"
