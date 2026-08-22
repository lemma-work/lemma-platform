"""The mark one run leaves on a tenant that every other run also uses.

The standing tenant is shared: run 50 opens the same pods run 1 opened, and
finds 49 runs' worth of history in them. That is the point — it is the condition
a real user is in, and the old design could never produce it. It also means a
run can no longer assume it is alone, so two things have to be true of every
durable thing a scenario makes.

**It says which run made it.** `run.name("orders")` gives `orders_scn7f3a1`. An
assertion filters to that, so "the table I made is there" stays provable in a pod
holding forty other tables, and a failure says which run to go and look at.

**It says a run made it at all.** The `scn` mark is what lets cleanup tell the
suite's leavings from a person's work. On a deployment that distinction is the
whole safety story: a reset that matched on shape rather than on this mark would
eventually delete something somebody wanted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

#: Stamped into every name a run creates. Short, pronounceable in a failure
#: message, and specific enough that matching on it is safe.
MARK = "scn"

#: An underscore, because a table name may hold nothing else: the datastore
#: refuses anything but alphanumerics and underscores, and a mark that only
#: worked on the resources with relaxed rules would be a mark scenarios learn
#: to leave off. One separator everywhere, valid everywhere.
JOIN = "_"

#: Anything a run has ever made, from this run or any other. This is what
#: `make scenarios-reset` matches on, so it is deliberately anchored to the end
#: of the name: a person who names a table `scn_forecast` is not caught by it.
MADE_BY_A_RUN = re.compile(rf"{JOIN}{MARK}[0-9a-f]{{4,}}$")


@dataclass(frozen=True, slots=True)
class Run:
    """One pass of the suite over the tenant."""

    id: str

    def name(self, what: str) -> str:
        """Name a durable thing so it is traceable to this run.

        The only sanctioned way for a scenario to name a table, a pod, an agent
        or anything else that outlives it. A literal name works right up until
        two runs overlap, and then it fails as a mysterious conflict in whichever
        run was second.
        """
        return f"{what}{JOIN}{MARK}{self.id}"

    def made_this(self, name: str) -> bool:
        """Did *this* run make it? What an assertion filters on."""
        return name.endswith(f"{JOIN}{MARK}{self.id}")

    def mine(self, names: object) -> list[str]:
        """The names among these that this run made, in order."""
        return [name for name in _names_of(names) if self.made_this(name)]

    def __str__(self) -> str:
        return f"{MARK}{self.id}"


def made_by_a_run(name: str) -> bool:
    """Did any run make it? What cleanup matches on."""
    return bool(MADE_BY_A_RUN.search(name))


def _names_of(rows: object) -> list[str]:
    if isinstance(rows, dict):
        rows = rows.get("items", [])
    found: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, str):
            found.append(row)
        elif isinstance(row, dict) and isinstance(row.get("name"), str):
            found.append(row["name"])
    return found


def begins() -> Run:
    """A new run. One per session; the session fixture holds it."""
    return Run(id=uuid4().hex[:6])
