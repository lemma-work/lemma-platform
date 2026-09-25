"""A ``control`` frame survives a deadlock instead of failing the frame.

A host's ``control`` frame and the five-minute dispatch cron both walk
`agent_host_run_leases` and `agent_host_commands`, and they used to walk them in
opposite orders: the frame takes command locks and then a *blocking* lease lock,
while the cron held a lease lock from `cancel_abandoned_host_runs` and then went
on to take command locks in `reconcile_expired_leases`. That is an ABBA cycle,
and Postgres aborts one side with 40P01.

The cron is now two transactions, which removes the cycle we know about. This
covers the one we do not: a deadlock is retried once, and the pass is idempotent
so re-running it is safe. Anything that is not a deadlock still propagates --
swallowing a real database error would hide it behind a silent retry.

It was a property of the long poll's handler; it is now one of the link's
store, and the test moved with it.
"""

from __future__ import annotations

from uuid import uuid7

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.agent.domain.agent_host import AgentHostCapacity, HostHello
from app.modules.agent.services.agent_host_link_store import (
    AgentHostLinkStore,
    ControlUpdates,
    is_deadlock,
)


class _Deadlock(DBAPIError):
    """A 40P01 shaped the way asyncpg surfaces it, through `orig.sqlstate`."""

    def __init__(self) -> None:
        super().__init__("UPDATE ...", {}, Exception("deadlock detected"))
        self.orig = type("_Orig", (), {"sqlstate": "40P01"})()


class _OtherDbError(DBAPIError):
    def __init__(self) -> None:
        super().__init__("UPDATE ...", {}, Exception("nope"))
        self.orig = type("_Orig", (), {"sqlstate": "23505"})()


class _ScriptedStore(AgentHostLinkStore):
    """The store with its one transaction replaced by a script of outcomes.

    A subclass at the seam the retry wraps, so the retry loop under test is the
    real one and only the database behind it is scripted.
    """

    def __init__(self, outcomes: list[object]) -> None:
        super().__init__(uow_factory=lambda: None)
        self._outcomes = outcomes
        self.attempts = 0

    async def _apply_control_once(self, **_kwargs):
        self.attempts += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def _apply(store: AgentHostLinkStore):
    return await store.apply_control(
        host_id=uuid7(),
        hello=HostHello(installation_id="i", host_release="1", protocol_version=3),
        updates=ControlUpdates(
            capacity=AgentHostCapacity(),
            acknowledged_command_ids=[],
            checkpoints=[],
            rejections=[],
        ),
    )


class TestTheDeadlockPredicate:
    def test_a_deadlock_is_recognised_by_sqlstate_not_message(self):
        assert is_deadlock(_Deadlock()) is True

    def test_another_database_error_is_not_a_deadlock(self):
        assert is_deadlock(_OtherDbError()) is False

    def test_an_error_with_no_driver_detail_is_not_a_deadlock(self):
        plain = DBAPIError("UPDATE ...", {}, Exception("boom"))
        plain.orig = None
        assert is_deadlock(plain) is False


class TestControlRetriesADeadlockOnce:
    async def test_the_second_attempt_is_the_one_that_answers(self):
        store = _ScriptedStore([_Deadlock(), ["a-command"]])

        assert await _apply(store) == ["a-command"]
        assert store.attempts == 2

    async def test_a_deadlock_on_both_attempts_still_surfaces(self):
        """Retried once, not forever. A persistent deadlock is news."""
        store = _ScriptedStore([_Deadlock(), _Deadlock()])

        with pytest.raises(DBAPIError):
            await _apply(store)
        assert store.attempts == 2

    async def test_any_other_database_error_is_not_retried(self):
        store = _ScriptedStore([_OtherDbError(), ["never"]])

        with pytest.raises(DBAPIError):
            await _apply(store)
        assert store.attempts == 1
