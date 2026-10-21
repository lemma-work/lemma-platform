"""Outbound surface deliveries on the work runtime, against real Postgres.

The ``surface.reply`` destination pump sends ``surface_deliveries`` rows with a
recording sender standing in for the platform, so each property is a fact
about the rows and the order the "platform" saw:

* a 429 with ``Retry-After`` puts the row back PENDING not before that long,
  and holds every later row for the destination behind it (no overtaking);
* 500 replies to one destination, with 429s injected, all arrive, once, in
  the order they were written;
* a sender killed mid-send leaves the row SENDING; once its deadline passes
  the pump marks it UNKNOWN and never sends it again.

The pump carries what has no channel line of its own (a held message, a reply
to somebody outside any run, a "please resend"). A run's answer is a line and
its channel delivery: ``test_run_answer_deliveries_e2e.py``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import dispatcher_for
from app.modules.agent_surfaces.domain.deliveries import (
    ConversationTarget,
    DeliveryContent,
)
from app.modules.agent_surfaces.services.surface_deliveries import (
    SendResult,
    stage_delivery,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    drain,
    scalar,
    surface_registry,
)

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("clean_surface_work")]


class Platform:
    """A recording platform: what arrived, in order, and what it refuses next."""

    def __init__(self) -> None:
        self.arrived: list[str] = []
        self.refuse: dict[str, float] = {}
        self.calls = 0

    async def __call__(self, row) -> SendResult:
        self.calls += 1
        text_ = row.content["text"]
        retry_after = self.refuse.pop(text_, None)
        if retry_after is not None:
            return SendResult(
                delivered=False, transient=True, retry_after=retry_after, error="429"
            )
        self.arrived.append(text_)
        return SendResult(delivered=True)


async def _stage(texts: list[str], *, destination: str, pod_id: UUID) -> None:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        for index, body in enumerate(texts):
            await stage_delivery(
                uow,
                idempotency_key=f"test:{destination}:{index}:{body}",
                target=ConversationTarget(conversation_id=uuid4()),
                content=DeliveryContent(text=body, purpose="notice"),
                destination_key=destination,
                pod_id=pod_id,
            )
        await uow.commit()


async def _statuses(destination: str) -> list[str]:
    async with async_session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT status FROM surface_deliveries WHERE destination_key = :d "
                "ORDER BY created_at, id"
            ),
            {"d": destination},
        )
        return [row[0] for row in rows.all()]


@pytest.mark.drives_work
async def test_a_retry_after_is_honoured_and_nothing_overtakes_it(test_pod):
    platform = Platform()
    registry = surface_registry(sender=platform)
    destination = f"group:{uuid4()}"
    platform.refuse["first"] = 120.0
    await _stage(
        ["first", "second"], destination=destination, pod_id=UUID(test_pod["id"])
    )

    await drain(registry)

    assert platform.arrived == []
    assert await _statuses(destination) == ["PENDING", "PENDING"]
    not_before = await scalar(
        "SELECT not_before FROM surface_deliveries WHERE content->>'text' = 'first' "
        "AND destination_key = :d",
        d=destination,
    )
    assert not_before - datetime.now(UTC) > timedelta(seconds=110)
    pump_due = await scalar(
        "SELECT run_at FROM work_items WHERE kind = 'surface.reply' AND dedup_key = :k",
        k=f"dest:{destination}",
    )
    assert pump_due - datetime.now(UTC) > timedelta(seconds=110), "the pump waits too"

    # The wait is over: both go, in the order they were written.
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE surface_deliveries SET not_before = now() "
                "WHERE destination_key = :d"
            ),
            {"d": destination},
        )
        await session.commit()
    await drain(registry, due=True)
    assert platform.arrived == ["first", "second"]
    assert await _statuses(destination) == ["SENT", "SENT"]


@pytest.mark.drives_work
async def test_five_hundred_replies_to_one_group_arrive_once_and_in_order(test_pod):
    platform = Platform()
    registry = surface_registry(sender=platform)
    destination = f"busy-group:{uuid4()}"
    texts = [f"reply {index:03d}" for index in range(500)]
    for index in range(0, 500, 50):
        platform.refuse[texts[index]] = 2.0  # a 429 every fifty messages
    await _stage(texts, destination=destination, pod_id=UUID(test_pod["id"]))

    rounds = 0
    while len(platform.arrived) < 500 and rounds < 50:
        rounds += 1
        async with async_session_maker() as session:
            await session.execute(
                text(
                    "UPDATE surface_deliveries SET not_before = now() "
                    "WHERE destination_key = :d AND status = 'PENDING'"
                ),
                {"d": destination},
            )
            await session.commit()
        await drain(registry, due=True)

    assert platform.arrived == texts, "nothing lost, nothing doubled, nothing reordered"
    assert set(await _statuses(destination)) == {"SENT"}
    # Ten 429s, each answered by waiting rather than by sending again at once.
    assert platform.calls == 510


@pytest.mark.drives_work
async def test_a_sender_killed_mid_send_leaves_unknown_and_is_never_resent(test_pod):
    """The worker dies with the message on the wire: it may have arrived."""
    sent: list[str] = []
    entered = asyncio.Event()

    async def hangs(row) -> SendResult:
        sent.append(row.content["text"])
        entered.set()
        await asyncio.sleep(3600)
        return SendResult(delivered=True)

    registry = surface_registry(sender=hangs, sending_lease=timedelta(seconds=1))
    destination = f"dm:{uuid4()}"
    await _stage(
        ["may have arrived", "after it"],
        destination=destination,
        pod_id=UUID(test_pod["id"]),
    )

    dispatcher = dispatcher_for(
        async_session_maker, WorkClass.DELIVERY, registry=registry
    )
    [item] = await dispatcher.claim_once()
    running = asyncio.create_task(dispatcher.execute(item))
    await asyncio.wait_for(entered.wait(), timeout=10)
    # SIGKILL: the task is gone and nothing it would have written gets written.
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    assert await _statuses(destination) == ["SENDING", "PENDING"]

    # Its lease lapses; the reaper hands the item to the next worker.
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE work_items SET state = 'running', "
                "lease_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": item.id},
        )
        await session.commit()
    await asyncio.sleep(1.1)  # past the row's own sending deadline

    platform = Platform()
    registry = surface_registry(sender=platform, sending_lease=timedelta(seconds=1))
    await drain(registry)  # the reaper takes the lapsed lease back (with a backoff)
    await drain(registry, due=True)

    assert await _statuses(destination) == ["UNKNOWN", "SENT"]
    assert platform.arrived == ["after it"], "the unknown one is never sent again"
    assert sent == ["may have arrived"]


@pytest.mark.drives_work
async def test_a_slow_send_is_retried_rather_than_left_to_go_unknown(test_pod):
    """A platform that does not answer within the send timeout is a retry."""
    platform = Platform()
    slow_once = True

    async def slow(row) -> SendResult:
        nonlocal slow_once
        if slow_once:
            slow_once = False
            await asyncio.sleep(3600)
        return await platform(row)

    registry = surface_registry(sender=slow, send_timeout=timedelta(seconds=0.2))
    destination = f"dm:{uuid4()}"
    await _stage(["late"], destination=destination, pod_id=UUID(test_pod["id"]))

    await drain(registry)
    assert await _statuses(destination) == ["PENDING"]
    assert "TimeoutError" in await scalar(
        "SELECT last_error FROM surface_deliveries WHERE destination_key = :d",
        d=destination,
    )
    async with async_session_maker() as session:  # the retry's wait, over
        await session.execute(
            text(
                "UPDATE surface_deliveries SET not_before = now() "
                "WHERE destination_key = :d"
            ),
            {"d": destination},
        )
        await session.commit()
    await drain(registry, due=True)

    assert await _statuses(destination) == ["SENT"]
    assert platform.arrived == ["late"]
