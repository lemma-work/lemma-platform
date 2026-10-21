"""How far a search's chunk query is narrowed before results come back.

Chunks live in the pod's own database. That database holds no authorization
data and cannot be joined to the file table, so a search cannot ask "and may
this caller read it?" in SQL. Something has to cross the gap, and there are
only two ways across:

* **Enumerate** — read the caller's readable file ids and send them, so the
  chunk query returns nothing they may not see. Exact, and the top ``limit``
  rows it returns are the true top ``limit``.
* **Post-filter** — run the chunk query unnarrowed, then authorize the file
  ids that actually came back. Bounded by the size of the candidate pool
  rather than by the pod, and the cost of that is recall: a caller who may
  read little of the pod can have their matches pushed out of the pool by
  matches they may not see.

Enumerating was the only mode this had, and it was unbounded: every search read
every file row in the pod to build the list. A pod large enough to make search
worth having is a pod large enough for that to be the dominant cost of it.

So the scope is worked out by measuring. Both sides of the pod's searchable
files are read -- the ones the caller may read and the ones they may not -- and
the shorter is sent: the readable ids as an exact filter, or the hidden ids as
an exclusion, which for an ordinary member is a handful of other people's
personal and restricted files. Only when both are over the ceiling does the
query run unnarrowed. Which branch ran is not a detail the search may ignore --
``enumerated`` false means *these results are not yet authorized*, and the
caller filters them. The name says so at every call site, because the failure
it guards against is someone reading "no ids" as "nothing to hide".

There is no state meaning "no filter was worked out". Omission is not
expressible: the argument is required, and every way of spelling it is a
decision someone made.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID


class SearchScopeTimedOut(Exception):
    """Working out the caller's searchable set took longer than search allows.

    Not an error to the caller: search falls back to authorizing the rows that
    come back, which is bounded by the candidate pool rather than by the pod.
    """


@dataclass(frozen=True, slots=True)
class SearchablePartition:
    """A pod's searchable files, split by whether the caller may read them.

    Either side may have been read only up to a limit; a side that reached it
    stands for "at least this many", which is all the choice between them
    needs.
    """

    readable: frozenset[UUID]
    hidden: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class SearchFileScope:
    #: Whether ``file_ids`` is the complete set of files the caller may read.
    #: False means the results are not yet authorized and the caller owes them
    #: an authorization pass, whether or not the query was narrowed.
    enumerated: bool
    #: The ids pushed into the chunk query: the readable set when
    #: ``enumerated``, the hidden set when ``excluding``, else empty.
    file_ids: frozenset[UUID]
    #: Whether ``file_ids`` are files to leave out rather than to keep.
    #:
    #: The hidden set is sent when it is the shorter of the two, which for an
    #: ordinary member it nearly always is. It names only *searchable* files the
    #: caller may not read, so a chunk left behind by anything else -- a file
    #: deleted or being reprocessed -- would get through it. That is why an
    #: excluding scope is not ``enumerated``: the results are still authorized
    #: afterwards. The pushdown is what keeps that pass from emptying the page.
    excluding: bool = False

    @classmethod
    def only(cls, file_ids: Iterable[UUID]) -> "SearchFileScope":
        """Exactly these files, and the chunk query is narrowed to them."""
        return cls(True, frozenset(file_ids))

    @classmethod
    def all_but(cls, file_ids: Iterable[UUID]) -> "SearchFileScope":
        """Everything except these files; the caller still authorizes results."""
        return cls(False, frozenset(file_ids), excluding=True)

    @classmethod
    def post_filtered(cls) -> "SearchFileScope":
        """Nothing is narrowed; the caller authorizes what comes back."""
        return cls(False, frozenset())

    @property
    def matches_nothing(self) -> bool:
        """True when no file can pass, so a query need not run at all."""
        return self.enumerated and not self.file_ids

    @property
    def narrowed(self) -> bool:
        """Whether the chunk query filters on file ids at all."""
        return self.enumerated or (self.excluding and bool(self.file_ids))

    @property
    def binds(self) -> bool:
        """Whether the statement takes the id array parameter."""
        return self.narrowed

    def sql_clause(self, column: str, parameter: str) -> str:
        """The predicate to AND into a chunk query, or "" for no narrowing."""
        if self.enumerated:
            return f"AND {column} = ANY(:{parameter})"
        if self.narrowed:
            return f"AND {column} <> ALL(:{parameter})"
        return ""

    def parameter_value(self) -> list[UUID]:
        return list(self.file_ids)
