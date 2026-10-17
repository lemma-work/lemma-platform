"""Who may read which line of a channel: every case of the one rule."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.channels.domain.entities import (
    ChannelMessage,
    Principal,
    Sender,
)
from app.modules.channels.domain.projection import Reader, may_read, readable

pytestmark = pytest.mark.unit

CHANNEL = uuid4()
WHATSAPP_GROUP = uuid4()
EMAIL_THREAD = uuid4()
TOM = uuid4()
PRIYA = uuid4()
LEM = uuid4()
_AT = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def _line(
    sender: Sender, *, source_id: UUID | None, members_only: bool = False
) -> ChannelMessage:
    return ChannelMessage(
        id=uuid4(),
        channel_id=CHANNEL,
        sender=sender,
        text="…",
        created_at=_AT,
        source_id=source_id,
        members_only=members_only,
    )


TOM_IN_THE_GROUP = _line(Sender.contact(TOM), source_id=WHATSAPP_GROUP)
PRIYA_IN_THE_GROUP = _line(Sender.member(PRIYA), source_id=WHATSAPP_GROUP)
ANSWER_FOR_TOM = _line(
    Sender.agent(LEM, on_behalf_of=Principal.contact(TOM)), source_id=WHATSAPP_GROUP
)
ANSWER_FOR_PRIYA = _line(
    Sender.agent(LEM, on_behalf_of=Principal.member(PRIYA)), source_id=WHATSAPP_GROUP
)
ON_THE_EMAIL_THREAD = _line(Sender.member(PRIYA), source_id=EMAIL_THREAD)
NOTE_IN_LEMMA = _line(Sender.member(PRIYA), source_id=None, members_only=True)
TYPED_IN_LEMMA = _line(Sender.member(PRIYA), source_id=None)

EVERYTHING = [
    TOM_IN_THE_GROUP,
    PRIYA_IN_THE_GROUP,
    ANSWER_FOR_TOM,
    ANSWER_FOR_PRIYA,
    ON_THE_EMAIL_THREAD,
    NOTE_IN_LEMMA,
    TYPED_IN_LEMMA,
]


def test_a_member_reads_every_line_of_every_source():
    assert readable(Reader.member(), EVERYTHING) == EVERYTHING


def test_a_contact_reads_only_their_own_source_and_nothing_a_member_was_answered():
    tom = Reader.contact(TOM, source_id=WHATSAPP_GROUP)

    assert readable(tom, EVERYTHING) == [
        TOM_IN_THE_GROUP,
        PRIYA_IN_THE_GROUP,
        ANSWER_FOR_TOM,
    ]


@pytest.mark.parametrize(
    "line",
    [ON_THE_EMAIL_THREAD, NOTE_IN_LEMMA, TYPED_IN_LEMMA, ANSWER_FOR_PRIYA],
    ids=["another-source", "private-note", "written-in-lemma", "a-members-answer"],
)
def test_a_contact_never_reads(line):
    assert not may_read(Reader.contact(TOM, source_id=WHATSAPP_GROUP), line)


def test_a_contact_reading_from_nowhere_reads_nothing():
    assert readable(Reader.contact(TOM, source_id=None), EVERYTHING) == []


def test_a_private_note_is_a_members_even_on_a_source():
    note = _line(Sender.member(PRIYA), source_id=WHATSAPP_GROUP, members_only=True)

    assert may_read(Reader.member(), note)
    assert not may_read(Reader.contact(TOM, source_id=WHATSAPP_GROUP), note)


def test_an_answer_whose_person_is_gone_is_read_by_members_only():
    orphaned = _line(
        Sender(kind=Sender.agent(LEM, on_behalf_of=Principal.member(PRIYA)).kind),
        source_id=WHATSAPP_GROUP,
    )

    assert may_read(Reader.member(), orphaned)
    assert not may_read(Reader.contact(TOM, source_id=WHATSAPP_GROUP), orphaned)


def test_a_sender_names_exactly_who_they_are():
    with pytest.raises(ValueError):
        Sender(kind=Sender.member(PRIYA).kind, contact_id=TOM)
    with pytest.raises(ValueError):
        Sender(
            kind=Sender.member(PRIYA).kind,
            user_id=PRIYA,
            on_behalf_of=Principal.member(PRIYA),
        )
    with pytest.raises(ValueError):
        Principal(user_id=PRIYA, contact_id=TOM)
