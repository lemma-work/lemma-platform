"""A run start that Postgres picked as a deadlock victim is admitted on the retry.

The admitting transaction is all or nothing, so a deadlock leaves no claim,
lease or command behind and running it again is safe. Without the retry, the
run failed outright on a database conflict that had nothing to do with it.
The lock order makes this rare; the retry is for the cycle nobody has found.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.agent_host.domain.host import AgentHostRunSpec
from app.modules.agent.agent_host.harness.dispatch import (
    _AdmittedRun,
    _commit_admission,
)
from app.modules.agent.agent_host.harness.run_config import (
    AgentHostRunConfig,
)

pytestmark = pytest.mark.unit


class _DriverError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


def _database(sqlstate: str) -> DBAPIError:
    return DBAPIError("INSERT INTO agent_host_commands ...", {}, _DriverError(sqlstate))


def _admitted() -> _AdmittedRun:
    harness_id = uuid4()
    return _AdmittedRun(
        run_spec=AgentHostRunSpec(
            agent_run_id=uuid4(),
            conversation_id=uuid4(),
            harness_id=harness_id,
            profile_revision="rev-1",
            model_name="gpt-5-codex",
            config_selections={},
            system_prompt="",
            prompt=[{"type": "text", "text": "Hello."}],
            run_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
        ),
        host_id=uuid4(),
        run_config=AgentHostRunConfig(
            harness_id=harness_id,
            runtime_profile_id=uuid4(),
            config_selections={},
            wait_timeout_seconds=60,
            model_name=None,
        ),
        encrypted_mcp={"encrypted": True},
        carried=frozenset(),
        instructions_digest="digest",
        credential_expires_at=None,
    )


class _ScriptedTransaction:
    """The admitting transaction, answering from a script instead of Postgres."""

    def __init__(self, outcomes: list[BaseException | None]) -> None:
        self._outcomes = outcomes
        self.attempts = 0

    async def __call__(
        self, _uow_factory: UnitOfWorkFactory, _admitted: _AdmittedRun
    ) -> None:
        self.attempts += 1
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome


def _no_database() -> UnitOfWorkFactory:
    raise AssertionError("the scripted transaction opens no unit of work")


async def test_a_deadlock_victim_is_admitted_on_the_second_attempt() -> None:
    transaction = _ScriptedTransaction([_database("40P01"), None])

    await _commit_admission(_no_database, _admitted(), transaction=transaction)

    assert transaction.attempts == 2


async def test_a_second_deadlock_is_not_retried_again() -> None:
    transaction = _ScriptedTransaction([_database("40P01"), _database("40P01")])

    with pytest.raises(DBAPIError):
        await _commit_admission(_no_database, _admitted(), transaction=transaction)
    assert transaction.attempts == 2


async def test_any_other_database_error_fails_at_once() -> None:
    transaction = _ScriptedTransaction([_database("23505"), None])

    with pytest.raises(DBAPIError):
        await _commit_admission(_no_database, _admitted(), transaction=transaction)
    assert transaction.attempts == 1
