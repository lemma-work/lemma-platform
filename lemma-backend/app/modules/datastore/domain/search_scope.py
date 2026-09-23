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

So the scope is a value with two states and the caller picks by measuring:
enumerate while the readable set is small enough to send, post-filter above
that. Which branch ran is not a detail the search may ignore --
``post_filtered()`` means *this query was not narrowed*, and the results are
unauthorized until the caller filters them. The name says so at every call
site, because the failure it guards against is someone reading "no ids" as
"nothing to hide".

There is no third state meaning "no filter was worked out". Omission is not
expressible: the argument is required, and both ways of spelling it are a
decision someone made.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SearchFileScope:
    #: Whether ``file_ids`` is the complete set of files the caller may read.
    #: False means the query was not narrowed at all and the caller owes the
    #: results an authorization pass.
    enumerated: bool
    #: The ids pushed into the chunk query. Empty unless ``enumerated``.
    file_ids: frozenset[UUID]

    @classmethod
    def only(cls, file_ids: Iterable[UUID]) -> "SearchFileScope":
        """Exactly these files, and the chunk query is narrowed to them."""
        return cls(True, frozenset(file_ids))

    @classmethod
    def post_filtered(cls) -> "SearchFileScope":
        """Nothing is narrowed; the caller authorizes what comes back."""
        return cls(False, frozenset())

    @property
    def matches_nothing(self) -> bool:
        """True when no file can pass, so a query need not run at all."""
        return self.enumerated and not self.file_ids

    @property
    def binds(self) -> bool:
        """Whether the statement takes the id array parameter."""
        return self.enumerated

    def sql_clause(self, column: str, parameter: str) -> str:
        """The predicate to AND into a chunk query, or "" for no narrowing."""
        return f"AND {column} = ANY(:{parameter})" if self.enumerated else ""

    def parameter_value(self) -> list[UUID]:
        return list(self.file_ids)
