"""Edits racing fires never leave a TIME schedule with two fire chains.

Randomized: several dispatchers fire due items while API requests edit,
pause and resume the same schedules, round after round. Between rounds, every
active schedule has exactly one fire item that can still fire (its ``fire_at``
equals the cursor), and every inactive one has none; and no occurrence ever
produced two runs.
"""

from __future__ import annotations

import asyncio
import os
import random

import pytest
from httpx import AsyncClient

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for, make_due
from app.modules.schedule.tests.e2e.trigger_work_support import (
    EVERY_HALF_HOUR,
    EVERY_QUARTER_HOUR,
    create_pod,
    create_time_schedule,
    scalar,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio, pytest.mark.drives_work]


SCHEDULES = 4
DISPATCHERS = 3

_VIABLE = """
SELECT s.id::text AS schedule_id, s.is_active,
       count(w.id) FILTER (
           WHERE w.state IN ('queued', 'running')
             AND (w.payload->>'fire_at')::timestamptz = s.next_fire_at
       ) AS viable
  FROM schedules s
  LEFT JOIN work_items w
    ON w.kind = 'schedule.fire' AND w.payload->>'schedule_id' = s.id::text
 WHERE s.pod_id = :pod
 GROUP BY s.id, s.is_active
"""


async def _viable(pod_id) -> list[tuple[str, bool, int]]:
    from sqlalchemy import text

    async with async_session_maker() as session:
        rows = (await session.execute(text(_VIABLE), {"pod": pod_id})).all()
    return [(row.schedule_id, row.is_active, row.viable) for row in rows]


@pytest.mark.parametrize(
    "rounds",
    [
        # Enough rounds to exercise every edit against a fire on each PR; the
        # long soak runs in the protected lane, where 40 rounds (~70s on CI)
        # do not hold up a merge.
        pytest.param(12, id="pr"),
        pytest.param(40, id="soak", marks=pytest.mark.slow),
    ],
)
async def test_edits_racing_fires_never_leave_two_fire_chains(
    authenticated_client: AsyncClient, fixed_test_org, rounds: int
):
    seed = int(os.environ.get("SCHEDULE_RACE_SEED", random.randrange(1 << 30)))
    rng = random.Random(seed)
    pod_id = await create_pod(authenticated_client, fixed_test_org["id"])
    ids = [
        (await create_time_schedule(authenticated_client, pod_id))["id"]
        for _ in range(SCHEDULES)
    ]
    fired_total = 0
    dispatchers = [
        dispatcher_for(async_session_maker, WorkClass.EVENTS, owner=f"d{n}")
        for n in range(DISPATCHERS)
    ]

    async def runs_per_schedule() -> dict[str, int]:
        from sqlalchemy import text

        async with async_session_maker() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT schedule_id::text, count(*) FROM schedule_runs "
                        "WHERE schedule_id = ANY(CAST(:ids AS uuid[])) GROUP BY 1"
                    ),
                    {"ids": ids},
                )
            ).all()
        return {row[0]: row[1] for row in rows}

    async def fire() -> int:
        """Cycles of: every due fire pulled to now, then all dispatchers claim at once.

        Only the item at a schedule's cursor may fire, so one cycle adds at
        most one run per schedule however many superseded items it pulled in.
        """
        executed = 0
        for _ in range(rng.randint(2, 5)):
            before = await runs_per_schedule()
            await make_due(async_session_maker, kind="schedule.fire")
            batches = await asyncio.gather(*(d.claim_once() for d in dispatchers))
            await asyncio.gather(
                *(
                    d.execute(item)
                    for d, batch in zip(dispatchers, batches)
                    for item in batch
                )
            )
            executed += sum(len(batch) for batch in batches)
            after = await runs_per_schedule()
            for schedule_id, count in after.items():
                assert count - before.get(schedule_id, 0) <= 1, (
                    f"seed={seed}: schedule {schedule_id} fired twice in one cycle"
                )
        return executed

    async def edit(schedule_id: str) -> None:
        await asyncio.sleep(rng.random() / 100)
        action = rng.choice(("cron", "pause", "resume", "cron"))
        body: dict[str, object]
        if action == "cron":
            body = {
                "config": {"cron": rng.choice((EVERY_QUARTER_HOUR, EVERY_HALF_HOUR))}
            }
        else:
            body = {"is_active": action == "resume"}
        response = await authenticated_client.patch(
            f"/pods/{pod_id}/schedules/{schedule_id}", json=body
        )
        assert response.status_code == 200, (seed, response.text)

    for round_number in range(rounds):
        results = await asyncio.gather(
            fire(),
            *(edit(sid) for sid in rng.sample(ids, rng.randint(1, SCHEDULES))),
        )
        fired_total += sum(r for r in results if isinstance(r, int))
        for schedule_id, active, viable in await _viable(str(pod_id)):
            assert viable == (1 if active else 0), (
                f"seed={seed} round={round_number} schedule={schedule_id} "
                f"active={active} viable={viable}"
            )

    duplicates = await scalar(
        "SELECT count(*) FROM (SELECT schedule_id, source_event_id FROM schedule_runs "
        "GROUP BY 1, 2 HAVING count(*) > 1) d"
    )
    assert duplicates == 0
    fires = await scalar(
        "SELECT count(*) FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id "
        "WHERE s.pod_id = :pod",
        pod=str(pod_id),
    )
    assert fires > 0, f"seed={seed}: nothing fired, so nothing raced"
    assert fired_total >= fires
