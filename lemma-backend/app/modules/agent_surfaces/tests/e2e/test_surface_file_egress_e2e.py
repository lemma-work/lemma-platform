"""E2E: file egress thresholds (F) over Telegram — small files attach natively,
large files (> the 5 MB soft cap) fall back to a tidy link card."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.platforms.attachment_limits import (
    SURFACE_INLINE_SOFT_BYTE_CAP,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    EmulatedAgentHarness,
    _create_surface,
    _process_ingress_and_emulate_reply,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages

pytestmark = pytest.mark.e2e


def _wire_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


async def _seed_pod_file(db_session, *, user_id, pod_id, name, content: bytes) -> str:
    from app.core.authorization.current import (
        reset_current_context,
        set_current_context,
    )
    from app.core.authorization.factory import create_authorization_data_service
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.datastore.api.dependencies import build_file_service

    uow = SqlAlchemyUnitOfWork(db_session)
    ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(ctx)
    try:
        entity = await build_file_service(uow).create_file(
            pod_id=pod_id,
            name=name,
            file_content=content,
            ctx=ctx,
            directory_path="/me/reports",
            search_enabled=False,
        )
        await uow.commit()
        return entity.path
    finally:
        reset_current_context(token)


async def _telegram_conversation(
    authenticated_client, db_session, pod_id, user_id, *, sender_id, message_id
) -> SurfaceChatContext:
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=user_id,
    )
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="here", message_id=message_id, sender_id=sender_id
            ),
            headers={},
        ),
        EmulatedAgentHarness(),
    )
    assert isinstance(context, SurfaceChatContext)
    return context


async def test_small_file_attaches_natively_on_telegram(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    _wire_telegram(monkeypatch, fake_telegram)
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    context = await _telegram_conversation(
        authenticated_client, db_session, pod_id, user_id, sender_id=444555666, message_id=91
    )
    path = await _seed_pod_file(
        db_session, user_id=user_id, pod_id=pod_id, name="small.pdf", content=b"%PDF-small"
    )

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    delivered = await handler.send_display_resource_for_conversation(
        conversation_id=context.conversation_id,
        request={"type": "FILE", "path": path},
    )
    assert delivered is True

    files = await wait_for_messages(message_store, "TELEGRAM_FILE", min_count=1)
    assert files[-1]["filename"] == "small.pdf"


async def test_large_file_falls_back_to_link_card_on_telegram(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    _wire_telegram(monkeypatch, fake_telegram)
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    context = await _telegram_conversation(
        authenticated_client, db_session, pod_id, user_id, sender_id=555666777, message_id=92
    )
    # Above the 5 MB soft cap → must be delivered as a link, not attached.
    big = b"x" * (SURFACE_INLINE_SOFT_BYTE_CAP + 1024)
    path = await _seed_pod_file(
        db_session, user_id=user_id, pod_id=pod_id, name="big.bin", content=big
    )

    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    delivered = await handler.send_display_resource_for_conversation(
        conversation_id=context.conversation_id,
        request={"type": "FILE", "path": path},
    )
    assert delivered is True

    # No native document upload happened; a card with a frontend link was sent.
    assert message_store.get_all("TELEGRAM_FILE") == []
    messages = await wait_for_messages(message_store, "TELEGRAM", min_count=2)
    rendered = " ".join(m.get("text", "") for m in messages)
    assert "app.example.test" in rendered
