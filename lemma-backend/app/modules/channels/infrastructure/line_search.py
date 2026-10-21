"""Full-text search over the lines a member may read, newest first.

A common word is in a large share of a pod's lines. Asked for "the newest
page of every match" with nothing to walk in time order, Postgres finds all
of them, reads each and sorts them, to show a page. With the pod's lines
indexed by when they were said (``ix_channel_messages_pod_time``) it walks
back from the newest instead, testing each line against the words, and stops
when the page is full; a rare word is still found through its own short
posting list in the text index (``ix_channel_messages_search``). The planner
picks between the two by how common the words are; ``LIMIT`` caps what
either reads. Within one channel, the channel's own index narrows the
matches to that channel's.

What is found is what the reader may see and reads in full -- an answer
withheld from them is not found by what it said -- filtered in the same
statement, before the page is cut.

Words are matched by how they start, so a search finds as it is typed:
"alph" finds "alpha". Each is looked up both stemmed (``english``: "run"
finds "running") and as written (``simple``: "runn" finds "running" too,
whose stem is "run"); a line must have every word. The stored vector holds
both forms of each word (``ChannelMessageModel.search``). Order stays newest
first -- the walk back in time depends on it -- with ``rank`` beside each
hit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import ChannelKind, ChannelMessage, SenderKind
from app.modules.channels.infrastructure.access import shown_in_full_to, visible_to
from app.modules.channels.infrastructure.models import (
    ChannelMessageModel,
    ChannelModel,
    ChannelQuestionModel,
)
from app.modules.channels.infrastructure.rows import MAX_LINES, message_entity

#: A snippet is the words around the match, unmarked: a reader highlights
#: the words itself, so nobody's line is ever rendered as markup. Whole words,
#: as written -- punctuation and all -- this many of them.
SNIPPET_WORDS = 24
#: How many of them come before the first match.
SNIPPET_BEFORE = 8


@dataclass(frozen=True, slots=True)
class SpeakerRef:
    kind: SenderKind
    id: UUID


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """What a search is narrowed to; every field absent is no narrowing."""

    channel_id: UUID | None = None
    kind: ChannelKind | None = None
    #: Who said it: a member, a contact, or an agent.
    sender: SpeakerRef | None = None
    #: Said at or after this; before ``until``.
    after: datetime | None = None
    until: datetime | None = None
    #: Archived channels are searched too.
    archived: bool = False


@dataclass(frozen=True, slots=True)
class SearchHit:
    message: ChannelMessage
    #: ``ts_rank`` of the line against the words: higher is closer.
    rank: float
    snippet: str | None


class LineSearch:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        viewer: Viewer,
        *,
        query: str,
        limit: int,
        within: SearchFilter | None = None,
        before: tuple[datetime, UUID] | None = None,
    ) -> list[SearchHit]:
        """Lines matching every word, newest first, in channels ``viewer`` may
        see and lines they read in full. Notes to members are members' to
        find. Paged by ``before`` (a hit's time and id)."""
        within = within or SearchFilter()
        if not words_of(query):
            return []
        rows = await self.session.execute(
            self._matching(
                viewer, query, within, before, limit=max(1, min(limit, MAX_LINES))
            )
        )
        return [
            SearchHit(
                message=message_entity(row[0], question=row[1]),
                rank=float(row[2] or 0.0),
                snippet=snippet(row[0].body, query),
            )
            for row in rows
        ]

    @staticmethod
    def _matching(
        viewer: Viewer,
        query: str,
        within: SearchFilter,
        before: tuple[datetime, UUID] | None,
        *,
        limit: int,
    ) -> Select[tuple[ChannelMessageModel, ChannelQuestionModel, float]]:
        message = ChannelMessageModel
        words = prefix_query(query)
        statement = (
            select(
                message,
                ChannelQuestionModel,
                func.ts_rank(message.search, words).label("rank"),
            )
            .outerjoin(
                ChannelQuestionModel, ChannelQuestionModel.message_id == message.id
            )
            .join(ChannelModel, ChannelModel.id == message.channel_id)
            .where(
                # The pod beside the words: one GIN (``btree_gin``) finds both.
                message.pod_id == viewer.pod_id,
                message.search.op("@@")(words),
                visible_to(viewer),
                shown_in_full_to(viewer),
                message.deleted_at.is_(None),
                *_narrowed(within),
            )
        )
        if before is not None:
            at, message_id = before
            statement = statement.where(
                or_(
                    message.created_at < at,
                    and_(message.created_at == at, message.id < message_id),
                )
            )
        return statement.order_by(message.created_at.desc(), message.id.desc()).limit(
            limit
        )


#: A word is letters and digits; everything else separates words, so nothing
#: a reader types reaches ``to_tsquery`` as its syntax.
_WORD = re.compile(r"[^\W_]+")
#: Words past this many are dropped: each is two lookups in the index.
MAX_WORDS = 8


def words_of(query: str) -> list[str]:
    return _WORD.findall(query.lower())[:MAX_WORDS]


def prefix_query(query: str) -> ColumnElement[str]:
    """Every word of ``query`` as the start of a word, stemmed or as written.

    A stop word (``english``: "the") is dropped from its stemmed half and
    still matched as written."""
    combined: ColumnElement[str] | None = None
    for word in words_of(query):
        start = f"{word}:*"
        stemmed = func.to_tsquery("english", start)
        written = func.to_tsquery("simple", start)
        # Most words are their own stem: then one lookup, not the same one
        # twice -- which the planner would count twice, and judge a rare word
        # common enough to walk back in time for. Folded before planning.
        either = case((stemmed == written, stemmed), else_=stemmed.op("||")(written))
        combined = either if combined is None else combined.op("&&")(either)
    if combined is None:
        return func.to_tsquery("simple", "")
    return combined


def _bare(word: str) -> str:
    """A word as the ``simple`` dictionary keys it: lower case, without the
    punctuation around it."""
    return word.strip(_PUNCTUATION).lower()


_PUNCTUATION = "".join(chr(c) for c in range(128) if not chr(c).isalnum())


def snippet(body: str, query: str) -> str | None:
    """The words of ``body`` around the first of the query's words, whole and
    as written -- a leading ``[`` or ``#`` kept -- with "…" where it was cut."""
    words = body.split()
    if not words:
        return None
    wanted = tuple(words_of(query))
    at = next(
        (index for index, word in enumerate(words) if _bare(word).startswith(wanted)),
        0,
    )
    start = max(0, min(at - SNIPPET_BEFORE, len(words) - SNIPPET_WORDS))
    end = min(len(words), start + SNIPPET_WORDS)
    shown = " ".join(words[start:end])
    return f"{'… ' if start > 0 else ''}{shown}{' …' if end < len(words) else ''}"


def _narrowed(within: SearchFilter) -> list[ColumnElement[bool]]:
    message = ChannelMessageModel
    found: list[ColumnElement[bool]] = []
    if not within.archived:
        found.append(ChannelModel.archived_at.is_(None))
    if within.channel_id is not None:
        found.append(message.channel_id == within.channel_id)
    if within.kind is not None:
        found.append(ChannelModel.kind == within.kind.value)
    if within.sender is not None:
        found += _said_by(within.sender)
    if within.after is not None:
        found.append(message.created_at >= within.after)
    if within.until is not None:
        found.append(message.created_at < within.until)
    return found


def _said_by(sender: SpeakerRef) -> list[ColumnElement[bool]]:
    message = ChannelMessageModel
    column = {
        SenderKind.MEMBER: message.sender_user_id,
        SenderKind.CONTACT: message.sender_contact_id,
        SenderKind.AGENT: message.sender_agent_id,
    }[sender.kind]
    return [message.sender_kind == sender.kind.value, column == sender.id]


__all__ = [
    "SNIPPET_WORDS",
    "LineSearch",
    "SearchFilter",
    "SearchHit",
    "SpeakerRef",
    "prefix_query",
    "snippet",
    "words_of",
]
