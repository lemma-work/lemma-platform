"""Two real workers booting together arm every periodic exactly once.

Both run ``python -m app.worker``; both arm at boot. The arming lock makes the
second see the first's items (queued or already running), so each periodic --
every former cron among them -- is one chain, not one per worker.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.tests.unit.test_periodic_work_registry import FORMER_CRONS
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.worker_process import production_worker_process

postgres_container = e2e_fixtures.postgres_container
redis_container = e2e_fixtures.redis_container
supertokens_container = e2e_fixtures.supertokens_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
db_manager = e2e_fixtures.db_manager
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend

# Slow: owns its worker processes, which must not share an e2e process with
# the session-scoped base worker.
pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]


async def _live_periodics() -> dict[str, int]:
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT dedup_key, count(*) FROM work_items "
                    "WHERE dedup_key LIKE 'periodic:%' "
                    "AND state IN ('queued', 'running') GROUP BY dedup_key"
                )
            )
        ).all()
    return {key.removeprefix("periodic:"): count for key, count in rows}


async def test_two_workers_booting_together_arm_each_periodic_once(
    e2e_settings, db_manager
) -> None:
    async with async_session_maker() as session, session.begin():
        await session.execute(
            text("DELETE FROM work_items WHERE dedup_key LIKE 'periodic:%'")
        )
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def worker(index: int) -> None:
        async with production_worker_process(
            e2e_settings, log_prefix=f"periodic_worker_{index}"
        ):
            ready[index].set()
            await release.wait()

    workers = [asyncio.create_task(worker(i)) for i in range(2)]
    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in ready)), timeout=180
        )
        live = await _live_periodics()
    finally:
        release.set()
        await asyncio.gather(*workers, return_exceptions=True)

    assert set(FORMER_CRONS) <= set(live), sorted(set(FORMER_CRONS) - set(live))
    assert {name: n for name, n in live.items() if n != 1} == {}
