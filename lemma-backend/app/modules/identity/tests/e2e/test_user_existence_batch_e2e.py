"""Asking which of a set of ids name real users.

This is the read that validates USER-typed datastore columns. It used to be one
``get`` per distinct id, which made a bulk import cost a round trip per row, and
the two properties it has to keep are easy to get wrong in opposite directions:
it must not invent users it did not find, and it must not drop users who have
left.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from app.modules.identity.infrastructure.models import User
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.test_support.query_counting import counted_queries

pytestmark = pytest.mark.e2e


def _repository(db_session) -> UserRepository:
    repository = UserRepository.__new__(UserRepository)
    repository.session = db_session
    return repository


async def _signed_up_user_id(scenario, db_session, prefix: str) -> UUID:
    """Sign somebody up and return the id this module knows them by.

    Resolved through the address rather than taken from the signup response,
    whose `id` belongs to the auth provider. These two happen to agree today,
    and a test that assumes so would fail somewhere unrelated the day they stop.
    """
    account = await scenario.create_user(prefix)
    user = await _repository(db_session).get_by_email(account["email"])
    assert user is not None and user.id is not None
    return user.id


async def test_existing_ids_answers_for_the_whole_set_in_one_statement(
    scenario, db_session
):
    await scenario.create_org_with_pod()
    real = {
        await _signed_up_user_id(scenario, db_session, f"batch{index}")
        for index in range(3)
    }
    invented = {uuid4() for _ in range(3)}

    with counted_queries() as statements:
        found = await _repository(db_session).existing_ids(real | invented)

    assert found == real, "invented ids came back as if they existed"
    reads = [text for text in statements if "users" in text]
    assert len(reads) == 1, (
        f"one question about a set of people should be one statement; got {len(reads)}"
    )


async def test_a_deactivated_or_deleted_user_still_exists(scenario, db_session):
    """The permissive half, and the one a stricter query would quietly break.

    A USER column records who a row belongs to. If this filtered the way
    sign-in does, every row naming someone who has left would stop being
    writable -- and the refusal would read "User does not exist", which is not
    what happened.
    """
    await scenario.create_org_with_pod()
    departed = await _signed_up_user_id(scenario, db_session, "departed")
    removed = await _signed_up_user_id(scenario, db_session, "removed")

    await db_session.execute(
        update(User).where(User.id == departed).values(is_active=False)
    )
    await db_session.execute(
        update(User).where(User.id == removed).values(is_deleted=True)
    )
    await db_session.commit()

    found = await _repository(db_session).existing_ids({departed, removed})

    assert found == {departed, removed}


async def test_an_empty_set_asks_nothing(db_session):
    """The guard that keeps `WHERE id IN ()` off the wire."""
    with counted_queries() as statements:
        found = await _repository(db_session).existing_ids(set())

    assert found == set()
    assert [text for text in statements if "users" in text] == []
