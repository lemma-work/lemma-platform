"""E2E for surface onboarding replies (WS6 / concern #3).

A signed-up user who is not a member of the surface's pod gets a pod-access
link only when the bot credential is pod-specific. Shared system bots avoid
disclosing a pod id to a non-member."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _ensure_connector_account,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.test_support.e2e_authz import signup_user

pytestmark = pytest.mark.e2e


async def _prepare_telegram_dm(
    db_session: AsyncSession,
    payload: dict,
    *,
    receiver_surface_ids: list[UUID] | None = None,
):
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=payload,
            receiver_surface_ids=receiver_surface_ids,
        )
    )
    await uow.commit()
    return context


async def test_system_bot_signed_up_non_member_does_not_get_pod_access_link(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "system-onboarding-bot")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    pod_id = test_pod["id"]

    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})

    outsider = await signup_user(async_client, "system-outsider")
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="701701701",
        resolved_user_id=UUID(outsider["id"]),
    )

    context = await _prepare_telegram_dm(
        db_session,
        _telegram_payload(text="let me in", message_id=6, sender_id=701701701),
    )

    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "surface_setup"
    message = context.reply_message or ""
    assert f"/pods/{pod_id}" not in message
    assert "set up or select a surface" in message.lower()


async def test_custom_bot_signed_up_non_member_gets_pod_access_link(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    pod_id = test_pod["id"]

    # A custom (user-credential) Telegram bot bound to this one pod.
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={"bot_token": "custom-onboarding-bot"},
    )
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(account.id)},
    )

    # A signed-up user who is NOT a member of this pod DMs the bot.
    outsider = await signup_user(async_client, "outsider")
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="700700700",
        resolved_user_id=UUID(outsider["id"]),
    )

    context = await _prepare_telegram_dm(
        db_session,
        _telegram_payload(text="let me in", message_id=5, sender_id=700700700),
        receiver_surface_ids=[UUID(surface["id"])],
    )

    # Instead of being dropped, they get a pod-access / join-request link.
    assert isinstance(context, SurfaceReplyContext)
    assert context.reply_kind == "pod_access"
    message = context.reply_message or ""
    assert "access" in message.lower()
    assert f"/pods/{pod_id}" in message
