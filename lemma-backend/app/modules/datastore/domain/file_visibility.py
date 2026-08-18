"""Which files a search may return, stated rather than implied.

The chunk queries used to take ``visible_file_ids: set[UUID] | None`` where
``None`` meant *no filter at all*. That is the wrong default for an
authorization parameter in two ways: forgetting to pass it returns everything
rather than failing, and there is no way to say "everything is visible" that is
distinguishable from "nobody worked out what is visible". Both spellings look
identical at the call site and only one of them is safe.

So the filter is a value with a stated direction, and it is a required
argument. "Everything" is ``excluding(())`` — still explicit, and impossible to
arrive at by omission.

The direction exists because either side can be the small one, and the array
travels to a different database than the one that computed it. In the observed
data most files in a pod are POD-visible and RESTRICTED is rare, so the *hidden*
side is usually the short list — often empty. Sending 16,050 ids to say "all of
them" was the wrong way round.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from uuid import UUID


class VisibilityDirection(str, Enum):
    ONLY = "only"
    EXCEPT = "except"


@dataclass(frozen=True, slots=True)
class FileVisibilityFilter:
    #: Which side ``file_ids`` names.
    direction: VisibilityDirection
    #: The ids pushed into the chunk query — the short side.
    file_ids: frozenset[UUID]
    #: Every file the pod actually has. Held in memory, never pushed.
    #:
    #: An exclude-direction filter says "everything but these", and a chunk
    #: whose file row no longer exists is not covered by "these". Chunk removal
    #: happens in a different database from the file delete, so a failure
    #: between the two leaves chunks behind — and under an exclude filter those
    #: orphans would match. Checking membership here costs one set lookup per
    #: returned row and closes that, whichever direction was pushed.
    known_file_ids: frozenset[UUID] = frozenset()

    @classmethod
    def only(cls, file_ids: Iterable[UUID]) -> "FileVisibilityFilter":
        """Exactly these files are visible."""
        ids = frozenset(file_ids)
        return cls(VisibilityDirection.ONLY, ids, ids)

    @classmethod
    def excluding(
        cls, file_ids: Iterable[UUID], *, known: Iterable[UUID]
    ) -> "FileVisibilityFilter":
        """Every known file except these is visible."""
        return cls(VisibilityDirection.EXCEPT, frozenset(file_ids), frozenset(known))

    @classmethod
    def smaller_of(
        cls, visible: Iterable[UUID], hidden: Iterable[UUID]
    ) -> "FileVisibilityFilter":
        """Whichever side is cheaper to send, given both.

        The pushdown shrinks; the in-process check does not. ``known`` is the
        union either way, so ``allows`` answers the same question regardless of
        which side travelled.
        """
        visible, hidden = frozenset(visible), frozenset(hidden)
        known = visible | hidden
        if len(hidden) < len(visible):
            return cls(VisibilityDirection.EXCEPT, hidden, known)
        return cls(VisibilityDirection.ONLY, visible, known)

    @property
    def matches_nothing(self) -> bool:
        """True when no file can pass, so a query need not run at all."""
        return self.direction is VisibilityDirection.ONLY and not self.file_ids

    @property
    def matches_everything(self) -> bool:
        """True when the *pushdown* restricts nothing.

        Not the same as "everything is visible": ``allows`` still rejects a
        file the pod does not have. This only says the chunk query needs no
        extra predicate.
        """
        return self.direction is VisibilityDirection.EXCEPT and not self.file_ids

    def allows(self, file_id: UUID) -> bool:
        if file_id not in self.known_file_ids:
            return False
        if self.direction is VisibilityDirection.ONLY:
            return file_id in self.file_ids
        return file_id not in self.file_ids

    def sql_clause(self, column: str, parameter: str) -> str:
        """The predicate to AND into a chunk query, or "" for no restriction."""
        if self.matches_everything:
            return ""
        if self.direction is VisibilityDirection.ONLY:
            return f"AND {column} = ANY(:{parameter})"
        return f"AND NOT ({column} = ANY(:{parameter}))"

    @property
    def binds(self) -> bool:
        return not self.matches_everything

    def parameter_value(self) -> list[UUID]:
        return list(self.file_ids)
