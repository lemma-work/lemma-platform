"""``lemma.work.oldest_transaction_age``: what an alert on the change-log horizon reads.

A transaction left open on a database holds its ``xmin`` horizon, and the
record change log delivers nothing past it. The work gauges sample the oldest
open transaction per database; this holds one open on real Postgres and reads
it back through the same sample the worker's gauge loop runs.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.core.infrastructure.work import gauges
from app.core.tests.e2e.work_support import WorkDb

pytestmark = pytest.mark.e2e

HOLD_SECONDS = 1.5


def _ages() -> dict[str, float]:
    return {
        obs.attributes["database"]: obs.value
        for obs in gauges._observe_transaction_age(None)  # type: ignore[arg-type]
    }


async def test_an_open_transaction_is_reported_with_its_age(work_db: WorkDb) -> None:
    async def other_database() -> float:
        async with work_db.engine.connect() as conn:
            return float(await conn.scalar(gauges.OLDEST_TRANSACTION_AGE) or 0.0)

    gauges.register_transaction_age_probe("datastore", other_database)
    try:
        async with work_db.engine.connect() as holder:
            await holder.execute(text("BEGIN"))
            await holder.execute(text("SELECT 1"))
            await asyncio.sleep(HOLD_SECONDS)
            await gauges.sample(work_db.sessions)
            held = _ages()
            await holder.execute(text("ROLLBACK"))
        await gauges.sample(work_db.sessions)
        released = _ages()
    finally:
        gauges._transaction_age_probes.pop("datastore", None)

    assert set(held) == {"main", "datastore"}
    assert held["main"] >= HOLD_SECONDS
    assert held["datastore"] >= HOLD_SECONDS
    # The sampler's own session is not counted, and nothing else is open.
    assert released["main"] < HOLD_SECONDS
