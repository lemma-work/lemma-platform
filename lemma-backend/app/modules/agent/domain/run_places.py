"""Where a run was asked, so that it answers there and nowhere else.

One conversation serves a person across every place of a channel -- their
email, their WhatsApp, Lemma's own channels -- so the conversation cannot say
where a turn was asked; only the run can. A run answers the place of the
message that started it. A message from another place, sent while it works, is
not taken into it: it waits for the run to end and is answered by a turn of its
own, where it was said. Taken in, the run read both, and its one answer went
wherever the person had last written: what was asked by email was answered on
WhatsApp, and the WhatsApp message was never answered at all.

A message's place is the channel source it was heard at (``asked_at_source``,
stamped by the server, never taken from a sender), or Lemma's own channels for
a line written there (``LEMMA_CHANNEL``, which the run already marks as
``asked_in_lemma``). A message typed into the conversation in Lemma has no
place: it joins whichever run is going, and is answered wherever that run's
answer goes. The place travels with the note-or-not kind of ``private_notes``
as what one run answers (``AnswerKind``): steering takes in only messages of
the run's own kind, and a follow-up turn answers the kind of the oldest
message waiting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.private_notes import (
    ASKED_AT_SOURCE_KEY,
    ASKED_IN_LEMMA_KEY,
    LEMMA_CHANNEL,
    PRIVATE_NOTE_KEY,
    asked_in_lemma,
    is_private_note,
    run_is_private,
)
from app.modules.agent.domain.value_objects import JsonObject

#: The place of a line written in Lemma's own channels.
IN_LEMMA = "lemma"


def _source(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def message_place(metadata: Mapping[str, object] | None) -> str | None:
    """Where a message was said: a source's id, ``IN_LEMMA``, or None."""
    source = _source((metadata or {}).get(ASKED_AT_SOURCE_KEY))
    if source is not None:
        return str(source)
    if (metadata or {}).get("source") == LEMMA_CHANNEL:
        return IN_LEMMA
    return None


def run_place(run_metadata: Mapping[str, object] | None) -> str | None:
    """Where the message that started a run was said; None when it had no place."""
    source = asked_at_source(run_metadata)
    if source is not None:
        return str(source)
    return IN_LEMMA if asked_in_lemma(run_metadata) else None


def asked_at_source(run_metadata: Mapping[str, object] | None) -> UUID | None:
    """The platform place a run answers, if it was asked on one."""
    return _source((run_metadata or {}).get(ASKED_AT_SOURCE_KEY))


def place_marks(place: str | None) -> JsonObject:
    """What a run answering this place is created with."""
    if place == IN_LEMMA:
        return {ASKED_IN_LEMMA_KEY: True}
    source = _source(place)
    return {ASKED_AT_SOURCE_KEY: str(source)} if source is not None else {}


@dataclass(frozen=True, slots=True)
class AnswerKind:
    """What one run answers: private notes or not, said at one place.

    A message with no place is of every place's kind.
    """

    notes: bool
    place: str | None

    @classmethod
    def of_message(cls, metadata: Mapping[str, object] | None) -> AnswerKind:
        return cls(notes=is_private_note(metadata), place=message_place(metadata))

    @classmethod
    def of_run(cls, run_metadata: Mapping[str, object] | None) -> AnswerKind:
        return cls(notes=run_is_private(run_metadata), place=run_place(run_metadata))

    def run_marks(self) -> JsonObject:
        """The marks a run answering this kind is created with."""
        marks: JsonObject = {PRIVATE_NOTE_KEY: True} if self.notes else {}
        return {**marks, **place_marks(self.place)}
