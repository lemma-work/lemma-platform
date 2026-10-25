"""One fake standing in for every repository a conversation service reads.

The services build their repositories from the unit of work
(``infrastructure/db``), one per aggregate. A unit test that drives the real
service with a hand-written fake points each of them at that fake, here, rather
than threading one object through every constructor.
"""

from __future__ import annotations

#: The attribute each repository is held under, on every service that holds one.
REPOSITORY_ATTRIBUTES = (
    "conversations",
    "runs",
    "messages",
    "queued_messages",
    "pausing_calls",
    "approval_decisions",
)

#: The coordinators a service holds, which hold repositories of their own.
_COORDINATORS = ("pauses", "approvals", "queries", "turns", "retries", "stops")


def wire_repositories[T](target: T, fake: object) -> T:
    """Point every repository ``target`` and its coordinators hold at ``fake``."""
    seen: set[int] = set()

    def visit(holder: object) -> None:
        if id(holder) in seen or not hasattr(holder, "__dict__"):
            return
        seen.add(id(holder))
        held = vars(holder)
        for name in REPOSITORY_ATTRIBUTES:
            if name in held:
                setattr(holder, name, fake)
        for name in _COORDINATORS:
            if held.get(name) is not None:
                visit(held[name])

    visit(target)
    return target
