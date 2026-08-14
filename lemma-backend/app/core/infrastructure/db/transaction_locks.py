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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


def safe_to_release(session: Any) -> bool:
    """Whether committing ``session`` now would only return its connection.

    A connection-scope release commits to hand the connection back. That is
    harmless when the transaction has done nothing but read, and destructive
    otherwise -- so this is the full list of reasons to leave it alone:

    * **Pending ORM work** (``new``/``dirty``/``deleted``): committing would
      make a caller's writes durable earlier than it asked.
    * **Flushed work** (``in_transaction`` with no pending ORM state). A
      ``flush()`` sends INSERTs without committing, and empties new/dirty/
      deleted -- so a repository that flushes and then authorizes looks clean
      while holding uncommitted writes. Committing there removes the caller's
      ability to roll back.
    * **Pending outbox events.** The unit of work stages events at ITS commit.
      Committing underneath it splits domain state and events across two
      transactions, and a crash in between keeps the state and loses the
      events -- exactly what the transactional outbox exists to prevent.
    * **A transaction-scoped advisory lock**, which is released by the commit.

    Read-only checks -- which is what authorization does -- satisfy all four.
    """
    # Read defensively, matching `_has_uncommitted_writes` below: a real session
    # always has these, a test double often does not, and refusing every release
    # when an attribute is missing would silently disable the feature under test.
    if any(getattr(session, attr, None) for attr in ("new", "dirty", "deleted")):
        return False
    if holds_transaction_scoped_lock(session):
        return False
    uow = None
    info = getattr(session, "info", None)
    if isinstance(info, dict):
        uow = info.get("lemma_uow")
    has_pending = getattr(uow, "has_pending_events", None)
    if callable(has_pending) and has_pending():
        return False
    in_transaction = getattr(session, "in_transaction", None)
    if callable(in_transaction) and in_transaction():
        # Reads open a transaction too, so this alone is not a veto -- but a
        # flushed write is indistinguishable from a read at this level, and
        # `_flushing`/`identity_map` do not survive a flush either. Ask the
        # connection whether anything is actually pending to be written.
        if _has_uncommitted_writes(session):
            return False
    return True


def _has_uncommitted_writes(session: Any) -> bool:
    """Whether a flush has already sent writes this transaction.

    SQLAlchemy tracks this per transaction: ``_transaction`` is marked dirty by
    a flush. Falls back to False when the attribute is absent (test doubles),
    because refusing every release would silently disable the feature.
    """
    transaction = getattr(session, "_transaction", None)
    return bool(getattr(transaction, "_dirty", False))


@asynccontextmanager
async def connection_released(session: Any) -> AsyncIterator[None]:
    """Hand the pooled connection back for the duration of the block.

    The problem this solves is that "I released the connection first" was only
    ever true in a comment. ``_release_after_authorization`` commits before its
    slow call and is correct -- and the static gate flags it anyway, because it
    is lexical and cannot see a commit inside a callee. Ten baselined violations
    were sites that were already right in substance.

    It also fixes what those helpers got wrong: they committed unconditionally,
    so a caller that had written something had its transaction ended for it.

    Making the release a block fixes both halves at once. At runtime it commits
    when ``safe_to_release`` allows (see that function for the four reasons it
    refuses, every one of which has bitten us). Statically, the gate recognises
    this context manager as *closing* the session scope, so an await inside the
    block is not a violation -- and an await outside it still is.

    Use it around the non-database work, not around the query::

        async with connection_released(session):
            await some_platform_api.send(payload)

    If ``safe_to_release`` refuses, the block still runs; the caller keeps its
    connection and the old behaviour. That is deliberate: ending a caller's
    transaction underneath it is worse than holding a connection.
    """
    commit = getattr(session, "commit", None)
    if callable(commit) and safe_to_release(session):
        await commit()
    yield
