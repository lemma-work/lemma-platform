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
#: A trailing extension is allowed, because a file keeps one — `notes_scn7f3a1`
#: and `notes_scn7f3a1.txt` are both this run's, and matching only the first
#: would leave every uploaded file invisible to cleanup.
MADE_BY_A_RUN = re.compile(rf"{JOIN}{MARK}[0-9a-f]{{4,}}(\.[A-Za-z0-9]{{1,8}})?$")


@dataclass(frozen=True, slots=True)
class Run:
    """One pass of the suite over the tenant."""

    id: str

    def name(self, what: str) -> str:
        """Name a durable thing so it is traceable to this run, and unique.

        The only sanctioned way for a scenario to name a table, a pod, an agent
        or anything else that outlives it. A literal name works right up until
        two runs overlap, and then it fails as a mysterious conflict in whichever
        run was second.

        It carries a unique part as well as the mark, and that is not belt and
        braces — it is the thing the shared tenant actually needs. The mark alone
        makes a name unique *between* runs; three scenarios of one run uploading
        `notes.txt` into the same standing pod still collide with each other.
        That is exactly what happened the first time this was tried, and the
        409 arrives in a fixture, so it reads as five broken scenarios rather
        than one name.

        Two calls therefore give two names. Hold the result if a scenario needs
        to say it twice — which is what a scenario asserting on what it made is
        doing anyway.
        """
        return f"{what}_{uuid4().hex[:6]}{JOIN}{MARK}{self.id}"

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


_current: Run | None = None


def current() -> Run:
    """This run. One per process, which is one per pytest session.

    A module-level singleton rather than something threaded through, because the
    steps need it: every one of them names things the caller did not name, and a
    default name that carries no mark is a resource cleanup cannot find and an
    assertion cannot filter to. Making the marked name the *default* name is
    what stops the scoping depending on anybody remembering it.
    """
    global _current
    if _current is None:
        _current = Run(id=uuid4().hex[:6])
    return _current


def a_name_for(noun: str) -> str:
    """A name for something this run is about to create.

    What every step uses when the caller named nothing. Same rule as
    `Run.name`, which is the point: a resource is marked and unique whether a
    scenario chose its name or let the harness choose.
    """
    return current().name(noun)


def begins() -> Run:
    """The run, for the session fixture that hands it to scenarios."""
    return current()
