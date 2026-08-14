"""Marking a session whose transaction must not be committed early.

Connection-scope work releases a pooled connection by committing as soon as the
reads are done, and guards that on the session having nothing pending --
``session.new``, ``session.dirty``, ``session.deleted``. That guard sees the ORM
identity map and nothing else.

A transaction can hold state the identity map knows nothing about. The one that
matters here is ``pg_advisory_xact_lock``: it is released *at commit*, which is
exactly why it is preferred over the session-scoped form (it cannot leak onto
the next borrower of a pooled connection). A caller that takes the lock, reads,
and then has its connection "helpfully" released has had its mutual exclusion
silently removed -- and finds out as a unique-violation from whoever it was
racing.

That is not hypothetical. It is how ``mkdir -p`` broke: the path lock was taken,
an authorization check released the connection because nothing was pending, and
two concurrent uploads both created the same parent folder.

So a caller taking transaction-scoped state marks the session, and the release
helpers leave a marked session alone until its own commit clears the mark.
"""

from __future__ import annotations

from typing import Any

_MARKER = "lemma_holds_transaction_scoped_lock"


def mark_transaction_scoped_lock(session: Any) -> None:
    """Declare that this transaction holds state that dies at commit."""
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info[_MARKER] = True


def holds_transaction_scoped_lock(session: Any) -> bool:
    """Whether committing this session now would drop a lock someone is using."""
    info = getattr(session, "info", None)
    return bool(isinstance(info, dict) and info.get(_MARKER))


def clear_transaction_scoped_lock(session: Any) -> None:
    """Forget the mark once the transaction that held the lock has ended."""
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        info.pop(_MARKER, None)
