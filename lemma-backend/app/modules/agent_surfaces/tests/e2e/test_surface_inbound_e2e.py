"""Inbound surface messages on the work runtime, against real Postgres.

Each delivery goes through the shipping path: the signed webhook route
classifies it in the request and stores one ``surface_inbound_messages`` row
plus its ``surface.ingest`` item in one transaction; the ingest item routes it
and, in its fenced completion, writes the message, the run, the run's
``agent.turn`` item and the row's ROUTED. Only the platform is a fake server.

What is pinned here:

* two bots numbering their updates from 1 are two deliveries, both answered;
* the same update redelivered -- at once, and three days later (the row is
  backdated, the retention job run) -- is one row and one run;
* an ingest that fails after taking its message claim is retried and routed
  once (the claim is the row's own);
* an ingest out of attempts goes FAILED and asks the sender to resend, once.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import RetryPolicy
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.services.surface_deliveries import SendResult
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
    a_telegram_bot,
    drain,
    scalar,
    surface_registry,
)

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("clean_surface_work")]


@pytest.fixture
def telegram_webhook_mode(monkeypatch):
    """A deployment where Telegram surfaces are driven by signed webhooks."""
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)


async def _post(client: AsyncClient, surface_id: str, secret: str, payload: dict):
    response = await client.post(
        f"/surfaces/{surface_id}/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers=build_telegram_secret_headers(secret),
    )
    assert response.status_code == 200, response.text


async def _bot(db_session, client, test_pod, fixed_test_user, fake_telegram, name):
    surface_id, secret, account = await a_telegram_bot(
        db_session,
        client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token=f"e2e-{name}-token",
        surface_name=name,
    )
    return surface_id, secret, account


async def _seed_sender(db_session, fixed_test_user, sender_id: int) -> None:
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )


async def _turns_for(surface_id: str) -> int:
    """``agent.turn`` items written for conversations on this surface."""
    return int(
        await scalar(
            "SELECT count(*) FROM work_items w "
            "JOIN surface_inbound_messages m "
            "ON w.payload->>'conversation_id' = m.conversation_id::text "
            "WHERE w.kind IN ('agent.turn', 'agent.turn.automation') "
            "AND m.surface_id = :surface",
            surface=surface_id,
        )
        or 0
    )


async def _user_messages(surface_id: str) -> int:
    return int(
        await scalar(
            "SELECT count(*) FROM agent_messages msg "
            "WHERE msg.role = 'user' AND msg.conversation_id IN ("
            "  SELECT conversation_id FROM surface_inbound_messages "
            "  WHERE surface_id = :surface)",
            surface=surface_id,
        )
        or 0
    )


@pytest.mark.drives_work
async def test_two_bots_numbering_from_one_are_two_deliveries_both_answered(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
):
    first, first_secret, account = await _bot(
        db_session,
        authenticated_client,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "first-bot",
    )
    second, second_secret, _ = await a_telegram_bot(
        db_session,
        authenticated_client,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        api_base=fake_telegram.api_base,
        bot_token="e2e-second-bot-token",
        surface_name="second-bot",
        account=account,
    )
    for sender in (6110001, 6110002):
        await _seed_sender(db_session, fixed_test_user, sender)
    to_first = _telegram_payload(
        text="Ask the first bot", message_id=8801, sender_id=6110001
    )
    to_second = _telegram_payload(
        text="Ask the second bot", message_id=9902, sender_id=6110002
    )
    to_first["update_id"] = to_second["update_id"] = 1

    await _post(authenticated_client, first, first_secret, to_first)
    await _post(authenticated_client, second, second_secret, to_second)
    await drain(surface_registry())

    routed = await scalar(
        "SELECT count(*) FROM surface_inbound_messages WHERE status = 'ROUTED' "
        "AND surface_id IN (:a, :b)",
        a=first,
        b=second,
    )
    assert routed == 2
    assert await _turns_for(first) == 1
    assert await _turns_for(second) == 1


@pytest.mark.drives_work
async def test_a_redelivery_now_and_three_days_later_is_answered_once(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
):
    surface_id, secret, _ = await _bot(
        db_session,
        authenticated_client,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "repeating-bot",
    )
    await _seed_sender(db_session, fixed_test_user, 6110003)
    payload = _telegram_payload(
        text="Say this once", message_id=8803, sender_id=6110003
    )
    registry = surface_registry()

    await _post(authenticated_client, surface_id, secret, payload)
    await _post(authenticated_client, surface_id, secret, payload)
    await drain(registry)
    assert await _turns_for(surface_id) == 1

    # Three days on (Meta retries for seven): the row is backdated, the daily
    # retention runs, and the platform tries once more.
    async with async_session_maker() as session:
        await session.execute(
            text(
                "UPDATE surface_inbound_messages SET received_at = :at "
                "WHERE surface_id = :surface"
            ),
            {"at": datetime.now(UTC) - timedelta(days=3), "surface": surface_id},
        )
        await session.commit()
    from app.modules.agent_surfaces.events.periodic_work import (
        INBOUND_KEPT,
        retention,
    )
    from app.core.infrastructure.work import WorkClass, WorkMode, WorkRegistry
    from app.modules.agent_surfaces.domain.work_payloads import NoPayload

    kept = WorkRegistry()
    kept.handler(
        kind="surfaces.retention",
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=NoPayload,
    )(retention)
    async with async_session_maker() as session:
        from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
        from app.core.infrastructure.work import SYSTEM_NAMESPACE, WorkWriter

        uow = SqlAlchemyUnitOfWork(session)
        await WorkWriter(session, registry=kept).add(
            "surfaces.retention", SYSTEM_NAMESPACE, NoPayload()
        )
        await uow.commit()
    from app.core.infrastructure.work.testing import run_until_idle

    await run_until_idle(async_session_maker, registry=kept, classes=[WorkClass.ADMIN])
    assert INBOUND_KEPT > timedelta(days=3)

    await _post(authenticated_client, surface_id, secret, payload)
    await drain(registry)

    stored = await scalar(
        "SELECT count(*) FROM surface_inbound_messages WHERE surface_id = :s",
        s=surface_id,
    )
    assert stored == 1
    assert await _turns_for(surface_id) == 1
    assert await _user_messages(surface_id) == 1


@pytest.mark.drives_work
async def test_an_ingest_that_failed_after_its_claim_is_routed_once_on_retry(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
    monkeypatch,
):
    """The message claim is the row's own: its retry is not its own duplicate.

    The first attempt prepares the message (taking and committing the claim)
    and then fails writing it. The Redis claim this replaced read that retry as
    a duplicate and dropped the message for good.
    """
    from app.modules.agent_surfaces.services import turn_starter

    surface_id, secret, _ = await _bot(
        db_session,
        authenticated_client,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "flaky-bot",
    )
    await _seed_sender(db_session, fixed_test_user, 6110004)
    real_write = turn_starter.write_inbound_message
    failures = {"left": 1}

    async def flaky_write(*args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("the database went away mid-write")
        return await real_write(*args, **kwargs)

    monkeypatch.setattr(turn_starter, "write_inbound_message", flaky_write)
    await _post(
        authenticated_client,
        surface_id,
        secret,
        _telegram_payload(text="Do not lose this", message_id=8804, sender_id=6110004),
    )
    registry = surface_registry()
    await drain(registry)
    attempts = await scalar(
        "SELECT attempts FROM work_items WHERE kind = 'surface.ingest'"
    )
    assert attempts == 1
    await drain(registry, due=True)

    assert (
        await scalar(
            "SELECT status FROM surface_inbound_messages WHERE surface_id = :s",
            s=surface_id,
        )
        == "ROUTED"
    )
    assert await _turns_for(surface_id) == 1
    assert await _user_messages(surface_id) == 1


@pytest.mark.drives_work
async def test_an_ingest_out_of_attempts_fails_and_asks_the_sender_once(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_telegram,
    telegram_webhook_mode,
    monkeypatch,
):
    from app.modules.agent_surfaces.services import turn_starter

    surface_id, secret, _ = await _bot(
        db_session,
        authenticated_client,
        test_pod,
        fixed_test_user,
        fake_telegram,
        "doomed-bot",
    )
    await _seed_sender(db_session, fixed_test_user, 6110005)

    async def always_fails(*_args, **_kwargs):
        raise RuntimeError("this message cannot be written")

    monkeypatch.setattr(turn_starter, "write_inbound_message", always_fails)
    sent: list[str] = []

    async def sender(row) -> SendResult:
        sent.append(row.content["text"])
        return SendResult(delivered=True)

    registry = surface_registry(sender=sender, ingest_retry=RetryPolicy(max_attempts=2))
    await _post(
        authenticated_client,
        surface_id,
        secret,
        _telegram_payload(text="Nobody hears this", message_id=8805, sender_id=6110005),
    )
    for _ in range(3):
        await drain(registry, due=True)

    assert (
        await scalar(
            "SELECT status FROM surface_inbound_messages WHERE surface_id = :s",
            s=surface_id,
        )
        == "FAILED"
    )
    assert (
        await scalar("SELECT state FROM work_items WHERE kind = 'surface.ingest'")
        == "dead"
    )
    assert sent == ["I couldn't process that message. Please send it again."]
    assert (
        await scalar(
            "SELECT status FROM surface_deliveries WHERE idempotency_key LIKE 'resend:%'"
        )
        == "SENT"
    )
