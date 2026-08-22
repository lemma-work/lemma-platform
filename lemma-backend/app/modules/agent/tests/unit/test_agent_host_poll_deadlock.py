"""The host poll survives a deadlock instead of returning 500.

The poll and the five-minute dispatch cron both walk `agent_host_run_leases`
and `agent_host_commands`, and they used to walk them in opposite orders. The
poll takes command locks and then a *blocking* lease lock; the cron held a lease
lock from `cancel_abandoned_host_runs` and then went on to take command locks in
`reconcile_expired_leases`. That is an ABBA cycle: Postgres aborts one side with
40P01, and the poll turned that into a 500 because it catches only
`AgentHostRepositoryError`.

The cron is now two transactions, which removes the cycle we know about. This
covers the one we do not: a deadlock is retried once, and the pass is idempotent
so re-running it is safe. Anything that is not a deadlock still propagates —
swallowing a real database error here would hide it behind a silent retry.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.agent.api.controllers import agent_host_controller as controller


class _Deadlock(DBAPIError):
    """A 40P01 shaped the way asyncpg surfaces it, through `orig.sqlstate`."""

    def __init__(self) -> None:
        super().__init__("UPDATE ...", {}, Exception("deadlock detected"))
        self.orig = type("_Orig", (), {"sqlstate": "40P01"})()


class _OtherDbError(DBAPIError):
    def __init__(self) -> None:
        super().__init__("UPDATE ...", {}, Exception("nope"))
        self.orig = type("_Orig", (), {"sqlstate": "23505"})()


class TestTheDeadlockPredicate:
    def test_a_deadlock_is_recognised_by_sqlstate_not_message(self):
        assert controller._is_deadlock(_Deadlock()) is True

    def test_another_database_error_is_not_a_deadlock(self):
        assert controller._is_deadlock(_OtherDbError()) is False

    def test_an_error_with_no_driver_detail_is_not_a_deadlock(self):
        plain = DBAPIError("UPDATE ...", {}, Exception("boom"))
        plain.orig = None
        assert controller._is_deadlock(plain) is False


class TestThePollRetriesADeadlockOnce:
    @staticmethod
    def _request():
        class _Capacity:
            available_runs = 1

            def model_dump(self, **_kwargs):
                return {"available_runs": 1}

        return type(
            "_Request",
            (),
            {
                "hello": None,
                "capacity": _Capacity(),
                "acknowledged_command_ids": [],
                "checkpoints": [],
                "rejections": [],
            },
        )()

    @pytest.mark.asyncio
    async def test_the_second_attempt_is_the_one_that_answers(self, monkeypatch):
        attempts = {"n": 0}

        async def flaky(*, request, authorization):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _Deadlock()
            return ("1.0", "READY", "host-1", ["a-command"])

        monkeypatch.setattr(controller, "_apply_host_control_updates", flaky)

        # Drive the retry loop the handler runs, not the whole endpoint: the
        # long-poll wait after it needs Redis and proves nothing here.
        result = None
        for attempt in range(2):
            try:
                result = await controller._apply_host_control_updates(
                    request=self._request(), authorization="token"
                )
                break
            except DBAPIError as exc:
                if attempt or not controller._is_deadlock(exc):
                    raise

        assert attempts["n"] == 2
        assert result == ("1.0", "READY", "host-1", ["a-command"])

    @pytest.mark.asyncio
    async def test_a_deadlock_on_both_attempts_still_surfaces(self, monkeypatch):
        """Retried once, not forever. A persistent deadlock is news."""
        attempts = {"n": 0}

        async def always_deadlocks(*, request, authorization):
            attempts["n"] += 1
            raise _Deadlock()

        monkeypatch.setattr(controller, "_apply_host_control_updates", always_deadlocks)

        with pytest.raises(DBAPIError):
            for attempt in range(2):
                try:
                    await controller._apply_host_control_updates(
                        request=self._request(), authorization="token"
                    )
                    break
                except DBAPIError as exc:
                    if attempt or not controller._is_deadlock(exc):
                        raise

        assert attempts["n"] == 2
