"""Where in a channel an answer goes: its thread, the line it answers, and the
place it is sent to.

One rule, whoever answers and wherever the line was said. What a platform
then makes of it -- a Slack ``thread_ts``, a Teams reply id, an email's
In-Reply-To, a quoted message on WhatsApp or Telegram -- is the platform's
translation of these three (``agent_surfaces.services.place_aim``), never a
second rule.

* **A line heard on a platform** is answered at its place, pointing at it. In
  a place that keeps threads (``THREADED``: a Slack or Teams channel) the
  answer is in the line's thread; elsewhere it is a message of the chat.
* **A line written in Lemma** is answered in Lemma. A member's own channel and
  a group read as a chat: in the main timeline, right after the line and
  pointing at it, carried to a group's place. A group mirroring a place that
  keeps threads keeps the habit: the answer is a reply in the line's thread.
  A contact's channel answers a member's line in its thread, off the
  contact's own line of talk, and carries it nowhere: it is the member's
  answer, made with their access, not the contact's to read.
* **A line already in a thread** is answered in that thread, everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.channels.domain.entities import (
    ChannelKind,
    ChannelMessage,
    SourceKind,
)

#: Platforms whose shared places keep threads: a reply there is in the thread
#: of what it answers, and a thread there is a thread here.
THREADED = frozenset({"SLACK", "TEAMS"})


def keeps_threads(*, kind: SourceKind, platform: str) -> bool:
    """Whether a place keeps threads: a shared place on a threaded platform."""
    return kind is SourceKind.CHAT and platform.upper() in THREADED


@dataclass(frozen=True, slots=True)
class Placement:
    """Where an answer goes: the thread it is in, the line it answers, and the
    place it is sent to (None: it stays in Lemma)."""

    thread_root_id: UUID | None
    reply_to_id: UUID | None
    target_source_id: UUID | None


def answer_to_heard(
    line: ChannelMessage, *, threads: bool, thread_root_id: UUID | None
) -> Placement:
    """An answer to a line heard on a platform: at its place, pointing at it,
    in ``thread_root_id`` -- the line its platform thread hangs from -- where
    the place ``threads``."""
    return Placement(
        thread_root_id=(line.thread_root_id or thread_root_id) if threads else None,
        reply_to_id=line.id,
        target_source_id=line.source_id,
    )


def answer_in_lemma(
    kind: ChannelKind | None, line: ChannelMessage, *, place_threads: bool = False
) -> Placement:
    """An answer to a line written in Lemma, in a channel of ``kind``.
    ``place_threads``: the place it was carried to keeps threads."""
    if line.thread_root_id is not None:
        in_thread: UUID | None = line.thread_root_id
    elif kind is ChannelKind.DIRECT:
        in_thread = None
    elif kind is ChannelKind.GROUP:
        in_thread = line.id if place_threads else None
    else:
        in_thread = line.id
    return Placement(
        thread_root_id=in_thread,
        reply_to_id=line.id,
        target_source_id=line.place_id if kind is ChannelKind.GROUP else None,
    )


__all__ = [
    "THREADED",
    "Placement",
    "answer_in_lemma",
    "answer_to_heard",
    "keeps_threads",
]
