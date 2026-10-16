"""Driving the surfaces module's work items from a test, against real Postgres.

A private ``WorkRegistry`` holds the real surfaces handlers (ingest, platform
update, the reply pump, the run-reply backstop), so ``run_until_idle`` runs
exactly those and leaves every other kind -- the ``agent.turn`` an ingest
writes, say -- queued, where the test can count it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import RetryPolicy, WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent_surfaces.domain.deliveries import SEND_TIMEOUT, SENDING_LEASE
from app.modules.agent_surfaces.events.ingest_work import register_surface_ingest_work
from app.modules.agent_surfaces.events.platform_update_work import (
    register_platform_update_work,
)
from app.modules.agent_surfaces.events.reply_for_run_work import (
    register_reply_for_run_work,
)
from app.modules.agent_surfaces.events.reply_work import register_surface_reply_work
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.services.delivery_sender import DeliverySender
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _create_surface,
    _ensure_connector_account,
)
from app.modules.connectors.infrastructure.models.account import Account

SURFACE_CLASSES = [WorkClass.INTERACTIVE, WorkClass.EVENTS, WorkClass.DELIVERY]


def surface_registry(
    *,
    sender: DeliverySender | None = None,
    ingest_retry: RetryPolicy | None = None,
    sending_lease: timedelta = SENDING_LEASE,
    send_timeout: timedelta = SEND_TIMEOUT,
) -> WorkRegistry:
    registry = WorkRegistry()
    register_surface_ingest_work(registry, retry=ingest_retry)
    register_platform_update_work(registry)
    register_surface_reply_work(
        registry,
        sender=sender,
        sending_lease=sending_lease,
        send_timeout=send_timeout,
    )
    register_reply_for_run_work(registry)
    return registry


async def drain(registry: WorkRegistry, *, due: bool = False) -> int:
    """Run the surfaces' due work until none is left. ``due``: pull delays in."""
    if due:
        await make_due(async_session_maker)
    return await run_until_idle(
        async_session_maker, registry=registry, classes=SURFACE_CLASSES
    )


async def rows(session_maker: Any, sql: str, **params: object) -> list[Any]:
    async with session_maker() as session:
        return list((await session.execute(text(sql), params)).all())


async def scalar(sql: str, **params: object) -> Any:
    async with async_session_maker() as session:
        return await session.scalar(text(sql), params)


async def a_telegram_bot(
    db_session: AsyncSession,
    client: AsyncClient,
    *,
    pod_id: str,
    user_id: str,
    api_base: str,
    bot_token: str,
    surface_name: str,
    account: Account | None = None,
) -> tuple[str, str, Account]:
    """One Telegram surface with a bot (and an agent) of its own.

    Returns the surface id, its webhook secret, and the account it is bound to,
    so a second bot can be built beside the first.
    """
    if account is None:
        connected = await _ensure_connector_account(
            db_session,
            user_id=user_id,
            connector_id="telegram",
            credentials={"bot_token": bot_token, "api_base_url": f"{api_base}/bot"},
        )
    else:
        connected = Account(
            user_id=account.user_id,
            organization_id=account.organization_id,
            auth_config_id=account.auth_config_id,
            connector_id=account.connector_id,
            provider_account_id=f"e2e-telegram-{surface_name}",
            credentials={"bot_token": bot_token, "api_base_url": f"{api_base}/bot"},
        )
        db_session.add(connected)
        await db_session.commit()
        await db_session.refresh(connected)
    owner = await _create_agent(client, pod_id)
    surface = await _create_surface(
        client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(connected.id)},
        name=surface_name,
        agent_name=owner["name"],
    )
    row = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert row is not None
    # A polled bot registers no webhook, and so has no secret.
    secret = (
        get_secret_cipher().decrypt_str(row.webhook_secret)
        if row.webhook_secret
        else ""
    )
    return surface["id"], secret, connected


async def mobile_changed(user_id: UUID) -> None:
    """Run what identity's ``UserMobileChanged`` fact set off, to completion.

    Emits nothing itself: identity writes the fact in the transaction that
    changes the number, so the effects a caller asserts afterwards are proof
    that it did. (A helper that emitted the fact made these tests pass while
    identity announced nothing, which is how the unbinding went missing once.)
    ``user_id`` names whose change is being settled; a session worker may
    already have run the item, which is equally fine.
    """
    from app.modules.agent_surfaces.events.notification_work import (
        register_notification_work,
    )

    del user_id
    registry = WorkRegistry()
    register_notification_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


@pytest.fixture
async def clean_surface_work():
    """Start from no surfaces rows and no queued surfaces work.

    An e2e session shares one database across its tests, and these tests count
    rows and items.
    """
    async with async_session_maker() as session:
        await session.execute(
            text(
                "DELETE FROM work_items WHERE kind LIKE 'surface%' "
                "OR kind LIKE 'surfaces.%' OR kind IN ('agent.turn', "
                "'agent.turn.automation', 'agent.title', 'agent.approval.execute', "
                "'agent.replies_arrived', 'notification.expire')"
            )
        )
        for table in (
            "surface_inbound_messages",
            "surface_deliveries",
            "surface_message_claims",
            "surface_receiver_state",
        ):
            await session.execute(text(f"DELETE FROM {table}"))
        await session.commit()
    yield


_REPLY_REGISTRY: WorkRegistry | None = None


async def deliver_replies() -> int:
    """Send what finished runs owe their surfaces, as the worker would.

    A run's answer is the ``surface_deliveries`` row its finalize writes
    (``surface.reply_for_run``), sent by the destination pump -- not the
    observer. A test that drives a run in-process runs those two kinds after.
    """
    global _REPLY_REGISTRY
    if _REPLY_REGISTRY is None:
        _REPLY_REGISTRY = WorkRegistry()
        register_reply_for_run_work(_REPLY_REGISTRY)
        register_surface_reply_work(_REPLY_REGISTRY)
    await make_due(async_session_maker)
    return await run_until_idle(
        async_session_maker, registry=_REPLY_REGISTRY, classes=[WorkClass.DELIVERY]
    )
