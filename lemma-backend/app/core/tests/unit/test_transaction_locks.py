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
