"""Where a run was asked, so that it answers there and nowhere else.

A person's conversation with an agent is kept on one platform of a channel --
their WhatsApp, their email, Lemma itself -- so their platforms no longer
share one (``uq_agent_conversations_channel_person``). A conversation can
still be reached from more than one place: a group's threads in Lemma, a line
another agent was named for, a message typed into the conversation itself. So
the run, not the conversation, says where a turn was asked. A run answers the
place of the message that started it. A message from another place, sent
while it works, is not taken into it: it waits for the run to end and is
answered by a turn of its own, where it was said. Taken in, the run read both,
and its one answer went wherever the person had last written: what was asked
by email was answered on WhatsApp, and the WhatsApp message was never answered
at all.

A message's place is the channel source it was heard at (set by the server,
never taken from a sender), or Lemma's own channels for a line written there
(``LEMMA_CHANNEL``, which the run it starts marks ``asked_in_lemma``). In a
group each thread is a place of its own (``lemma:<thread>``): a line in another
thread waits for the run answering one, as a platform's message does, and is
answered by a turn of its own in its thread; a reply in the same thread joins
it. A member's own channel reads as one chat, one place. It is one fact with
one record: the string ``ASKED_IN_PLACE_KEY`` holds in the metadata of the
message and of the run, written once with the row. A message typed into the
conversation in Lemma has no place: it joins whichever run is going, and is
answered wherever that run's answer goes. The place travels with the
note-or-not mark of ``private_notes`` as what one run answers
(``AnswerScope``): steering takes in only messages of the run's own scope, and
a follow-up turn answers the scope of the oldest message waiting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.private_notes import LEMMA_CHANNEL
from app.modules.agent.domain.run_state import RunPrivacy
from app.modules.agent.domain.value_objects import MessageRole

#: The place of a line written in Lemma's own channels.
IN_LEMMA = "lemma"

#: Where a message, and the run it starts, was asked: a channel source's id,
#: or a place in Lemma as ``in_lemma`` names it. Written by the server with
#: the row and never after; the database compares it as stored and never
#: builds one, so steering cannot drift from the name the code gives a place.
ASKED_IN_PLACE_KEY = "asked_in_place"

#: On a line written in a group in Lemma, and on the run it starts: the thread
#: it is in -- its root line, or the line itself at the top of the group.
ASKED_IN_THREAD_KEY = "asked_in_thread"


def thread_of(metadata: Mapping[str, object] | None) -> UUID | None:
    """The group thread a message, or run, was asked in, if any."""
    value = (metadata or {}).get(ASKED_IN_THREAD_KEY)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def in_lemma(thread: UUID | None) -> str:
    """The place of a line written in Lemma: its group thread, or Lemma. The
    one place its name is made (``ASKED_IN_PLACE_KEY``)."""
    return f"{IN_LEMMA}:{thread}" if thread is not None else IN_LEMMA


def message_place(
    asked_at_source: UUID | None, *, source: str | None, thread: UUID | None = None
) -> str | None:
    """Where a message was said: a source's id, Lemma (in a group, its
    thread), or None.

    ``source`` is the label its caller gave it (``turn_admission.message_source``).
    """
    if asked_at_source is not None:
        return str(asked_at_source)
    return in_lemma(thread) if source == LEMMA_CHANNEL else None


def run_place(run: RunPrivacy) -> str | None:
    """Where the message that started a run was said; None when it had no place."""
    if run.asked_at_source is not None:
        return str(run.asked_at_source)
    return in_lemma(run.asked_in_thread) if run.asked_in_lemma else None


def stored_place(metadata: Mapping[str, object] | None) -> str | None:
    """The place stored with a message or a run, if it has one."""
    value = (metadata or {}).get(ASKED_IN_PLACE_KEY)
    return value if isinstance(value, str) and value else None


def place_source(place: str | None) -> UUID | None:
    """The channel source a place is, when it is one."""
    if place is None or place.startswith(IN_LEMMA):
        return None
    try:
        return UUID(place)
    except ValueError:
        return None


def _thread(place: str | None) -> UUID | None:
    prefix = f"{IN_LEMMA}:"
    if place is None or not place.startswith(prefix):
        return None
    try:
        return UUID(place.removeprefix(prefix))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class AnswerScope:
    """What one run answers: private notes or not, said at one place.

    A message with no place is in every place's scope.
    """

    notes: bool
    place: str | None

    @classmethod
    def of_run(cls, run: RunPrivacy) -> AnswerScope:
        return cls(notes=run.private_note, place=run_place(run))

    def privacy(self) -> RunPrivacy:
        """The marks a run answering this scope is created with."""
        return RunPrivacy(
            private_note=self.notes,
            asked_in_lemma=self.place is not None and self.place.startswith(IN_LEMMA),
            asked_in_thread=_thread(self.place),
            asked_at_source=place_source(self.place),
        )

    def takes(self, message: AnswerScope) -> bool:
        """Whether a run of this scope may read ``message`` -- the rule
        steering applies in SQL (``db/queued_messages``)."""
        return message.notes == self.notes and message.place in (None, self.place)


def without_queued_elsewhere(
    messages: Sequence[Message], *, run_id: UUID, run: RunPrivacy
) -> list[Message]:
    """``messages`` less what waits for a turn of its own.

    A message sent while a run worked, of a scope that run does not answer, is
    owed by the follow-up turn that claims it, and read by that turn only. In
    the history of any other run -- the one it waited behind, a run started
    before its follow-up -- it is left out: read there, the run took it in
    without claiming it, the run log counted it read, and its own turn found
    nothing left to answer.
    """
    scope = AnswerScope.of_run(run)
    return [
        message
        for message in messages
        if not _waits_elsewhere(message, run_id=run_id, scope=scope)
    ]


def _waits_elsewhere(message: Message, *, run_id: UUID, scope: AnswerScope) -> bool:
    """Still owed, unclaimed by ``run_id``, and outside its scope."""
    state = message.state
    if message.role is not MessageRole.USER or not state.is_queued:
        return False
    if state.steered_into_run_id == run_id:
        return False
    return not scope.takes(
        AnswerScope(notes=state.private_note, place=stored_place(message.metadata))
    )
