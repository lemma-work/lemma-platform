"""The channel around a run's messages, read from the timeline as it runs.

A message a run answers in a channel keeps which lines of the channel it was
handed (``channels.contracts.backgrounds``), never what they said; a member's
line written into a contact's conversation keeps which line it was. Both are
read here, as the run's history is put together, into the in-memory message
the prompt renders: ``channel_context`` for the background block
(``harnesses/channel_context``), the follow-up's own text for the line. Never
written back -- the stored message stays a reference, so an edit, a deletion
or a forgotten contact reaches every later prompt.

The member's own line a turn answers is read the same way. Its words are
stored as the turn's message -- the conversation shows them -- but the prompt
reads the line as it says now: edited, it reads as edited; deleted, the
model is told it was deleted, and never what it said.

Only the messages of runs carried whole get their background: an older run
is reduced to its requests and answer anyway (``runtime_history``), and its
background would only be read to be thrown away. The cut moves in whole
blocks of runs, so a provider's cached prefix survives it.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from pydantic import ValidationError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import FOLLOW_UP_LINE_KEY
from app.modules.agent.domain.entities import Message, MessageKind, MessageRole
from app.modules.agent.domain.private_notes import LEMMA_CHANNEL
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundRefs,
    current_words,
    read_backgrounds,
)

logger = get_logger(__name__)

#: What the prompt reads for a member's line deleted since it was answered.
DELETED_LINE = "(This message was deleted by its author.)"


def is_follow_up(message: Message) -> bool:
    metadata = message.metadata or {}
    return message.kind is MessageKind.NOTIFICATION and bool(metadata.get("follow_up"))


async def with_channel_background(
    uow: SqlAlchemyUnitOfWork,
    messages: Sequence[Message],
    *,
    background_of_runs: Collection[UUID] | None = None,
) -> list[Message]:
    """``messages`` as the prompt reads them, the channel read in.

    ``background_of_runs``: the runs whose messages get their background;
    every message's when ``None``. A follow-up whose line is gone is gone too;
    a member's line is read as it says now.
    """
    backgrounds, follow_ups, said = _references(messages, background_of_runs)
    if not backgrounds and not follow_ups and not said:
        return list(messages)
    shown = await read_backgrounds(uow, [refs for _, refs in backgrounds])
    words = await current_words(uow, [*follow_ups.values(), *said.values()])
    read = list(messages)
    for (index, refs), background in zip(backgrounds, shown, strict=True):
        metadata = dict(read[index].metadata or {})
        metadata["channel_context"] = [
            line.model_dump(mode="json") for line in background.lines
        ]
        metadata["channel_context_withheld"] = background.withheld
        metadata["channel_context_view"] = refs.view.value
        read[index] = read[index].model_copy(update={"metadata": metadata})
    for index, line_id in follow_ups.items():
        text = words.get(line_id)
        if text is not None:
            read[index] = read[index].model_copy(update={"text": text})
    for index, line_id in said.items():
        read[index] = _as_said_now(read[index], words.get(line_id))
    return [
        message
        for index, message in enumerate(read)
        if index not in follow_ups or follow_ups[index] in words
    ]


def _as_said_now(message: Message, now: str | None) -> Message:
    """A member's line as it says now: ``now`` is ``None`` for one deleted
    since -- or emptied, which says as much as deleted."""
    if now is None and (message.text or "").strip():
        now = DELETED_LINE
    if now is None or now == message.text:
        return message
    return message.model_copy(update={"text": now})


def _references(
    messages: Sequence[Message], background_of_runs: Collection[UUID] | None
) -> tuple[list[tuple[int, BackgroundRefs]], dict[int, UUID], dict[int, UUID]]:
    """Which messages keep a background, which follow-ups name a line, and
    which are a member's line in Lemma -- by their place in ``messages``."""
    backgrounds: list[tuple[int, BackgroundRefs]] = []
    follow_ups: dict[int, UUID] = {}
    said: dict[int, UUID] = {}
    for index, message in enumerate(messages):
        metadata = message.metadata or {}
        if is_follow_up(message):
            line_id = _uuid(metadata.get(FOLLOW_UP_LINE_KEY))
            if line_id is not None:
                follow_ups[index] = line_id
            continue
        if message.role is MessageRole.USER and metadata.get("source") == LEMMA_CHANNEL:
            line_id = _uuid(metadata.get("channel_message_id"))
            if line_id is not None:
                said[index] = line_id
        refs = metadata.get(BACKGROUND_KEY)
        wanted = (
            background_of_runs is None or message.agent_run_id in background_of_runs
        )
        parsed = _refs(refs, message) if refs is not None and wanted else None
        if parsed is not None:
            backgrounds.append((index, parsed))
    return backgrounds, follow_ups, said


def _refs(value: object, message: Message) -> BackgroundRefs | None:
    try:
        return BackgroundRefs.model_validate(value)
    except ValidationError:
        # Written by this module's own turn starters, so a shape it cannot
        # read is a bug -- not a reason to fail the run it rides on.
        logger.warning(
            "agent.channel_background.unreadable.degraded",
            message_id=str(message.id),
            exc_info=True,
        )
        return None


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except ValueError:
        return None


__all__ = [
    "DELETED_LINE",
    "is_follow_up",
    "with_channel_background",
]
