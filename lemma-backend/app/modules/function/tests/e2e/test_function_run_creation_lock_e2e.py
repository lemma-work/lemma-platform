"""Creating runs of one function does not serialise them; changing it waits.

QA: under a DATASTORE trigger that started a workflow running one function per
record write, every run creation took FOR UPDATE on the function row. They
queued behind each other, hit statement timeouts, and record writes ran about
five times slower. Run creation only needs the active revision to hold still.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.infrastructure.repositories import FunctionRepository
from app.modules.function.tests.e2e.function_work_support import seed_function

pytestmark = pytest.mark.e2e


async def test_run_creations_share_the_function_and_a_change_waits_for_them(
    db_manager, test_pod, fixed_test_user
) -> None:
    async with db_manager.session_factory() as session:
        function = await seed_function(
            session,
            pod_id=UUID(test_pod["id"]),
            user_id=UUID(fixed_test_user["id"]),
            mode="complete",
        )
        await session.commit()
        function_id = function.id

    async with (
        db_manager.session_factory() as first,
        db_manager.session_factory() as second,
        db_manager.session_factory() as change,
    ):
        assert await FunctionRepository(SqlAlchemyUnitOfWork(first)).get_for_share(
            function_id
        )
        # A second run creation is not held up by the first.
        await second.execute(text("SET LOCAL lock_timeout = '1s'"))
        assert await FunctionRepository(SqlAlchemyUnitOfWork(second)).get_for_share(
            function_id
        )
        # A revision change still waits for both.
        await change.execute(text("SET LOCAL lock_timeout = '500ms'"))
        with pytest.raises(DBAPIError, match="lock timeout"):
            await FunctionRepository(SqlAlchemyUnitOfWork(change)).get_for_update(
                function_id
            )
        await change.rollback()
        await first.rollback()
        await second.rollback()
