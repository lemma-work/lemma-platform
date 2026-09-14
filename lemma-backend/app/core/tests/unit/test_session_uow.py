"""Reaching a unit of work from a session, and what happens when there isn't one.

Services are constructed from a session, not from the unit of work wrapping it,
so a service that wants to defer a cache invalidation past the commit -- or end
the transaction before slow work -- has only the session to ask. Three services
had each grown their own copy of that lookup before it moved here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.infrastructure.db.session_uow import (
    SESSION_UOW_KEY,
    active_uow,
    commit_now,
)


class _Uow:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _repository(uow: object | None) -> SimpleNamespace:
    info = {} if uow is None else {SESSION_UOW_KEY: uow}
    return SimpleNamespace(session=SimpleNamespace(info=info))


def test_a_repository_and_its_session_both_resolve() -> None:
    """Call sites hold one or the other, and neither has to say which it is.

    `AsyncSession` has no `session` attribute, so the disambiguation needs no
    flag and no second function.
    """
    uow = _Uow()
    repository = _repository(uow)

    assert active_uow(repository) is uow
    assert active_uow(repository.session) is uow


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(SimpleNamespace(), id="no-session"),
        pytest.param(SimpleNamespace(session=SimpleNamespace()), id="no-info"),
        pytest.param(SimpleNamespace(session=SimpleNamespace(info={})), id="no-uow"),
        pytest.param(
            SimpleNamespace(session=SimpleNamespace(info="not a mapping")),
            id="info-is-not-a-mapping",
        ),
    ],
)
def test_an_incomplete_chain_is_no_unit_of_work_rather_than_an_error(source) -> None:
    """Doubles vary, and a missing unit of work is a fact, not a failure.

    Every caller treats `None` as "do the work inline", which is correct: no
    unit of work is also no pooled connection to hand back.
    """
    assert active_uow(source) is None


@pytest.mark.asyncio
async def test_commit_now_ends_the_transaction_and_tolerates_its_absence() -> None:
    uow = _Uow()

    await commit_now(_repository(uow))
    assert uow.commits == 1

    await commit_now(SimpleNamespace())
    assert uow.commits == 1
