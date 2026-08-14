"""A connection release must not drop a lock the caller is still using.

The release helpers hand a pooled connection back by committing once the reads
are done, guarded on the session having nothing pending. That guard reads the
ORM identity map, which cannot see a ``pg_advisory_xact_lock`` -- and that lock
is released *by the commit*.

This is not theoretical: it is how ``mkdir -p`` broke. The path lock was taken,
an authorization check released the connection because nothing was pending, and
two concurrent uploads created the same parent folder. The e2e test caught it;
these pin the mechanism so the next person changing a release helper sees the
rule rather than rediscovering it.
"""

from __future__ import annotations

from app.core.infrastructure.db.transaction_locks import (
    clear_transaction_scoped_lock,
    holds_transaction_scoped_lock,
    mark_transaction_scoped_lock,
    safe_to_release,
)


class _Session:
    def __init__(self) -> None:
        self.info: dict = {}


def test_an_unmarked_session_is_free_to_release() -> None:
    assert holds_transaction_scoped_lock(_Session()) is False


def test_a_marked_session_must_not_be_committed_early() -> None:
    session = _Session()
    mark_transaction_scoped_lock(session)
    assert holds_transaction_scoped_lock(session) is True


def test_the_mark_clears_when_the_transaction_ends() -> None:
    """Otherwise one lock would suppress every later release in the request."""
    session = _Session()
    mark_transaction_scoped_lock(session)
    clear_transaction_scoped_lock(session)
    assert holds_transaction_scoped_lock(session) is False


def test_a_session_without_usable_info_is_treated_as_unlocked() -> None:
    """Test doubles routinely lack `.info`; that must not crash a release.

    Answering "no lock" is also the safe default here -- the alternative would
    silently disable connection release everywhere the attribute is missing.
    """

    class _NoInfo:
        pass

    assert holds_transaction_scoped_lock(_NoInfo()) is False
    mark_transaction_scoped_lock(_NoInfo())  # must not raise
    clear_transaction_scoped_lock(_NoInfo())  # must not raise


class _Releasable:
    """A session that has only read: the case a release exists for."""

    def __init__(self) -> None:
        self.info: dict = {}
        self.new: list = []
        self.dirty: list = []
        self.deleted: list = []
        self._transaction = None

    def in_transaction(self) -> bool:
        return True


class _Uow:
    def __init__(self, pending: bool) -> None:
        self._pending = pending

    def has_pending_events(self) -> bool:
        return self._pending


def test_a_read_only_session_may_be_released() -> None:
    assert safe_to_release(_Releasable()) is True


def test_pending_orm_work_blocks_release() -> None:
    session = _Releasable()
    session.dirty = ["something"]
    assert safe_to_release(session) is False


def test_staged_outbox_events_block_release() -> None:
    """Committing under the unit of work would split state from its events.

    The events are staged at the UoW's own commit. Commit early and the domain
    writes land in one transaction and the events in the next -- a crash
    between them keeps the change and loses the notification, which is the
    exact failure the transactional outbox exists to prevent.
    """
    session = _Releasable()
    session.info["lemma_uow"] = _Uow(pending=True)
    assert safe_to_release(session) is False

    session.info["lemma_uow"] = _Uow(pending=False)
    assert safe_to_release(session) is True


def test_flushed_writes_block_release() -> None:
    """A flush empties new/dirty/deleted while leaving writes uncommitted.

    So the obvious guard reports a clean session, and committing takes away the
    caller's ability to roll those writes back.
    """
    session = _Releasable()
    session._transaction = type("_T", (), {"_dirty": True})()
    assert safe_to_release(session) is False


def test_an_advisory_lock_blocks_release() -> None:
    session = _Releasable()
    mark_transaction_scoped_lock(session)
    assert safe_to_release(session) is False
