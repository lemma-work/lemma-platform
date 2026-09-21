"""Claiming a bot is serialized, because checking alone does not claim anything.

`ensure_unique_platform_identity` reads who holds a bot and then the caller
writes the surface. Both happen in one transaction, and `create` flushes without
committing -- so without a lock two of them can read "nobody holds it" and both
commit, arriving at the ambiguous routing the rule exists to prevent by a
different road.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)

pytestmark = pytest.mark.asyncio


def _repository() -> tuple[SurfaceRepository, AsyncMock]:
    session = AsyncMock()
    session.execute.return_value = MagicMock(first=MagicMock(return_value=None))
    uow = MagicMock()
    uow.session = session
    return SurfaceRepository(uow), session


async def _claim(repository: SurfaceRepository, *, bot: str = "U_LEMMABOT"):
    return await repository.get_platform_identity_holder(
        pod_id=uuid4(),
        platform="SLACK",
        external_workspace_id="T_ACME",
        surface_identity_id=bot,
    )


async def test_the_claim_takes_a_transaction_scoped_lock_before_it_reads():
    """The lock is first, or the read answers about a moment already gone."""
    repository, session = _repository()

    await _claim(repository)

    first_statement, first_parameters = session.execute.await_args_list[0].args
    assert str(first_statement) == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert isinstance(first_parameters["lock_key"], int)
    # Transaction-scoped, so it outlives this call and covers the write the
    # caller makes next. Anything shorter would be a lock over the read alone,
    # which is the thing that is already safe.
    assert "xact" in str(first_statement)


async def test_one_bot_in_one_workspace_is_one_key():
    """Two callers racing for the same bot must contend, or the lock is decor."""
    first, first_session = _repository()
    second, second_session = _repository()

    await _claim(first)
    await _claim(second)

    assert (
        first_session.execute.await_args_list[0].args[1]["lock_key"]
        == second_session.execute.await_args_list[0].args[1]["lock_key"]
    )


async def test_a_different_bot_is_a_different_key():
    """A second Slack app in the workspace is a different identity, and must not
    queue behind the first -- `PS-SURF-001` allows it deliberately."""
    first, first_session = _repository()
    second, second_session = _repository()

    await _claim(first, bot="U_LEMMABOT")
    await _claim(second, bot="U_SECONDBOT")

    assert (
        first_session.execute.await_args_list[0].args[1]["lock_key"]
        != second_session.execute.await_args_list[0].args[1]["lock_key"]
    )


async def test_the_key_does_not_carry_the_workspace_or_the_bot():
    """A lock key is visible in `pg_locks`; the workspace it guards is not."""
    repository, session = _repository()

    await _claim(repository)

    parameters = session.execute.await_args_list[0].args[1]
    assert "T_ACME" not in str(parameters)
    assert "U_LEMMABOT" not in str(parameters)
