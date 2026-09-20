"""What identity's sender lookups ask before they name a person.

These four statements are how an inbound chat message becomes an identity an
agent run executes as, so each one is an authority grant. They are asserted
here, beside the surfaces that consume them, and on the compiled SQL: a missing
predicate widens the result set, which downstream reads as a successful match
rather than as an error.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.identity.infrastructure.user_repositories import UserRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _EmptyResult()

    async def scalar(self, statement):
        self.statements.append(statement)


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _Uow:
    def __init__(self, session) -> None:
        self.session = session


async def _sql(call) -> str:
    session = _CapturingSession()
    await call(UserRepository(_Uow(session)))
    assert len(session.statements) == 1
    return str(
        session.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


async def test_a_telegram_handle_names_a_verified_account_or_nobody():
    """The handle is a claim, so the account behind it must be a real one.

    `telegram_username` is free text on a profile: nothing checks that the
    person who typed it holds the handle. Its sibling `get_ids_by_mobile_numbers`
    has always required `is_verified` for that reason, and this one -- which is
    tried *first*, before the filtered email path -- did not.
    """
    sql = await _sql(lambda users: users.get_live_id_by_telegram_lower("asha"))

    assert "users.is_active IS true" in sql
    assert "users.is_deleted IS false" in sql
    assert "users.is_verified IS true" in sql


async def test_the_taken_handle_question_still_sees_everybody():
    """The other half of the split stays unfiltered, and must.

    It stands in front of a uniqueness check that counts departed rows too, so
    filtering it would let a second person claim a freed handle and then fail on
    the insert instead of being refused cleanly.
    """
    sql = await _sql(lambda users: users.get_id_by_telegram_lower("asha"))

    assert "is_active" not in sql
    assert "is_deleted" not in sql


async def test_re_checking_a_cached_id_asks_the_weakest_live_predicate():
    """Not `is_verified`, on purpose.

    The email lookup resolves an unverified account, so requiring more here
    would refuse on a second message somebody the first message accepted.
    """
    sql = await _sql(lambda users: users.get_live_id(uuid4()))

    assert "users.is_active IS true" in sql
    assert "users.is_deleted IS false" in sql
    assert "is_verified" not in sql


async def test_an_email_match_still_excludes_departed_colleagues():
    sql = await _sql(lambda users: users.get_id_by_email_insensitive("a@b.test"))

    assert "users.is_active IS true" in sql
    assert "users.is_deleted IS false" in sql
