"""Native receivers as leased work items: one poller per credential, failover by lease.

Two dispatchers stand in for two workers. The Telegram bot's
``surface.receiver`` item is the lease: whichever dispatcher holds it polls
the (fake) Bot API; the other one claims nothing. Killing the holder lets the
lease lapse, the other takes the item over and resumes from the offset kept
in ``surface_receiver_state`` -- so an update the dead worker had already
stored is neither stored twice nor skipped, and the one after it arrives.

The Telegram manager bot, which used to be polled by every flagged worker,
gets the same single lease.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    WorkClass,
    WorkRegistry,
    WorkWriter,
)
from app.core.infrastructure.work.testing import (
    dispatcher_for,
    make_due,
    run_until_idle,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.work_payloads import NoPayload
from app.modules.agent_surfaces.events.receiver_work import (
    RECEIVERS_RECONCILE,
    register_receiver_work,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _telegram_payload
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    a_telegram_bot,
    scalar,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("clean_surface_work")]


async def _reconcile(registry: WorkRegistry) -> None:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await WorkWriter(session, registry=registry).add(
            RECEIVERS_RECONCILE, SYSTEM_NAMESPACE, NoPayload()
        )
        await uow.commit()
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


async def _stored_updates() -> list[str]:
    async with async_session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT payload->>'update_id' FROM surface_inbound_messages "
                "ORDER BY received_at"
            )
        )
        return [row[0] for row in rows.all()]


@pytest.mark.drives_work
async def test_a_dead_pollers_bot_is_taken_over_from_its_stored_offset(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(surface_settings, "enable_slack_socket_mode", False)
    monkeypatch.setattr(surface_settings, "enable_resend_polling_mode", False)
    monkeypatch.setattr(surface_settings, "telegram_bot_token", None)
    token = "e2e-failover-token"
    await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token=token,
        surface_name="failover-bot",
    )
    registry = WorkRegistry()
    register_receiver_work(registry, manager_wanted=lambda: False)
    await _reconcile(registry)
    await _reconcile(registry)  # idempotent: still one item
    assert (
        await scalar("SELECT count(*) FROM work_items WHERE kind = 'surface.receiver'")
        == 1
    )

    first = dispatcher_for(
        async_session_maker, WorkClass.RECEIVERS, registry=registry, owner="w1"
    )
    second = dispatcher_for(
        async_session_maker, WorkClass.RECEIVERS, registry=registry, owner="w2"
    )
    [item] = await first.claim_once()
    polling = asyncio.create_task(first.execute(item))
    assert await second.claim_once() == [], "a second worker must not poll the same bot"

    update_a = _telegram_payload(
        text="before the crash", message_id=501, sender_id=8001
    )
    fake_telegram.queue_update(update_a, bot_token=token)
    await eventually(
        label="the first update stored",
        probe=_stored_updates,
        done=lambda stored: stored == [str(update_a["update_id"])],
        timeout_seconds=15,
    )

    # SIGKILL the holder. Its lease lapses; the reaper hands the item on.
    polling.cancel()
    await asyncio.gather(polling, return_exceptions=True)
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE work_items SET state = 'running', lease_owner = 'w1:receivers', "
                "lease_until = now() - interval '1 second' WHERE id = :id"
            ),
            {"id": item.id},
        )
        await session.commit()
    assert await second.reap() == 1
    await make_due(async_session_maker)

    # Telegram still holds A for anyone who asks below its offset, and B is new.
    update_b = _telegram_payload(text="after the crash", message_id=502, sender_id=8001)
    fake_telegram.queue_update(update_a, bot_token=token)
    fake_telegram.queue_update(update_b, bot_token=token)
    [taken] = await second.claim_once()
    assert taken.id == item.id and taken.lease_losses == 1
    polling = asyncio.create_task(second.execute(taken))
    try:
        await eventually(
            label="the update after the crash stored",
            probe=_stored_updates,
            done=lambda stored: str(update_b["update_id"]) in stored,
            timeout_seconds=15,
        )
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)

    assert await _stored_updates() == [
        str(update_a["update_id"]),
        str(update_b["update_id"]),
    ], "no update stored twice, none skipped"
    resumed = [
        poll["offset"]
        for poll in message_store.get_all("TELEGRAM_GET_UPDATES")
        if poll["count"] and poll["offset"]
    ]
    assert resumed and resumed[0] == update_a["update_id"] + 1, resumed
    assert await scalar(
        "SELECT cursor FROM surface_receiver_state WHERE cursor = :c",
        c=str(update_b["update_id"] + 1),
    )


@pytest.mark.drives_work
async def test_the_manager_bot_is_polled_by_exactly_one_worker():
    starts: list[str] = []

    class _Manager:
        def __init__(self, owner: list[str]) -> None:
            self._owner = owner

        async def run(self) -> None:
            starts.append("started")
            await asyncio.sleep(3600)

    registry = WorkRegistry()
    register_receiver_work(
        registry,
        manager=lambda _uow_factory: _Manager(starts),
        manager_wanted=lambda: True,
    )
    await _reconcile(registry)
    workers = [
        dispatcher_for(
            async_session_maker, WorkClass.RECEIVERS, registry=registry, owner=f"w{n}"
        )
        for n in range(3)
    ]
    claimed = [await worker.claim_once() for worker in workers]
    held = [items for items in claimed if items]
    assert len(held) == 1 and len(held[0]) == 1
    running = asyncio.create_task(workers[claimed.index(held[0])].execute(held[0][0]))
    await asyncio.sleep(0.2)
    assert starts == ["started"]
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
