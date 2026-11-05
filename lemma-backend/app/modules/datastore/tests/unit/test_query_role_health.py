"""The datastore role probe: whether pods can be given their query roles.

The engine is the probe's collaborator, stood in for where it lives; the
database's answer is scripted, one per connect.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.modules.datastore.contracts import query_role_health

pytestmark = pytest.mark.unit

_ENGINE = "app.modules.datastore.infrastructure.session.get_datastore_engine"


class _Engine:
    def __init__(self, answers: list[object]) -> None:
        self.answers = answers

    @asynccontextmanager
    async def connect(self):
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer

        class _Result:
            def scalar(self) -> object:
                return answer

        class _Conn:
            async def execute(self, _statement) -> _Result:
                return _Result()

        yield _Conn()


async def test_a_privilege_revoked_after_ok_is_noticed(monkeypatch) -> None:
    """`ok` used to be latched for the life of the process, so a revoked
    CREATEROLE went on reading `ok` while every new pod's queries failed."""
    engine = _Engine([True, False])
    monkeypatch.setattr(_ENGINE, lambda: engine)

    first = await query_role_health.datastore_role_state(recheck_after=0)
    second = await query_role_health.datastore_role_state(recheck_after=0)

    assert (first, second) == ("ok", "missing_createrole")


async def test_a_refused_connection_is_unknown_not_a_verdict(monkeypatch) -> None:
    engine = _Engine([ConnectionRefusedError(61, "refused")])
    monkeypatch.setattr(_ENGINE, lambda: engine)

    state = await query_role_health.datastore_role_state(recheck_after=0)

    assert state == "unknown"


async def test_a_failed_check_is_not_answered_with_the_ok_before_it(
    monkeypatch,
) -> None:
    """Until the next check, the answer is the last one seen: `unknown`, not an
    `ok` the failed check could no longer confirm."""
    engine = _Engine([True, ConnectionRefusedError(61, "refused")])
    monkeypatch.setattr(_ENGINE, lambda: engine)

    await query_role_health.datastore_role_state(recheck_after=0)
    failed = await query_role_health.datastore_role_state(recheck_after=0)
    cached = await query_role_health.datastore_role_state(recheck_after=3600)

    assert (failed, cached) == ("unknown", "unknown")
