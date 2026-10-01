"""A pod's own bot in groups, through its own webhook -- end to end.

A pod that brings its own Telegram bot or its own WhatsApp number is delivered
to at ``/surfaces/{id}/webhook``, not the platform's shared one. Groups there
are the same as on a shared bot: a group the number created is confirmed when
WhatsApp says so, everything said is logged, a message nobody put to the bot is
left alone, and the bot answers to the name the app shows it by.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.composition import (
    build_app_event_handler,
    build_surface_ingress,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.group_updates import apply_group_updates
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _seed_external_user,
    _set_user_mobile_number,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e

PHONE_NUMBER_ID = "1234567890"
BUSINESS_NUMBER = "15550001111"
GROUP = "HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA"
MEMBER = "15550555555"
CLIENT = "16505551234"
TELEGRAM_GROUP = -1009876543210
MEMBER_TELEGRAM_ID = 900100
STRANGER_TELEGRAM_ID = 777000


def _wire_whatsapp(monkeypatch, fake_whatsapp) -> None:
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.client._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")


def _wire_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


def _whatsapp_body(field: str, value: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-001", "changes": [{"field": field, "value": value}]}],
    }


def _said_in_whatsapp_group(text: str, *, sender: str) -> dict:
    return _whatsapp_body(
        "messages",
        {
            "messaging_product": "whatsapp",
            "metadata": {
                "display_phone_number": BUSINESS_NUMBER,
                "phone_number_id": PHONE_NUMBER_ID,
            },
            "contacts": [{"profile": {"name": "Tiago"}, "wa_id": sender}],
            "messages": [
                {
                    "from": sender,
                    "group_id": GROUP,
                    "id": f"wamid.{uuid4().hex}",
                    "timestamp": "1744344500",
                    "text": {"body": text},
                    "type": "text",
                }
            ],
        },
    )


def _said_in_telegram_group(text: str, *, sender_id: int, mention: bool) -> dict:
    message: dict = {
        "message_id": 6000 + abs(hash(text)) % 1000,
        "from": {"id": sender_id, "is_bot": False, "first_name": "Tom"},
        "chat": {"id": TELEGRAM_GROUP, "type": "supergroup", "title": "Launch crew"},
        "date": 1700000100,
        "text": text,
    }
    if mention:
        message["entities"] = [{"type": "mention", "offset": 0, "length": 9}]
    return {"update_id": message["message_id"], "message": message}


async def test_an_own_whatsapp_number_confirms_logs_and_answers_its_groups(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    _wire_whatsapp(monkeypatch, fake_whatsapp)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "WHATSAPP"}
    )
    surface_id = UUID(surface["id"])
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=MEMBER
    )
    started = await authenticated_client.post(
        f"/pods/{pod_id}/groups",
        json={"surface_name": surface["name"], "title": "Acme order 1182"},
    )
    assert started.status_code == 201, started.text
    request_id = message_store.get_all("WHATSAPP_GROUP_CREATE")[-1]["request_id"]

    # WhatsApp confirms it on the number's own webhook.
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await apply_group_updates(
            uow,
            SurfaceDirectWebhookIngress(
                surface_id=surface_id,
                payload=_whatsapp_body(
                    "group_lifecycle_update",
                    {
                        "messaging_product": "whatsapp",
                        "groups": [
                            {
                                "group_id": GROUP,
                                "type": "group_create",
                                "request_id": request_id,
                                "subject": "Acme order 1182",
                            }
                        ],
                    },
                ),
            ),
            adapters=SurfacePlatformAdapterRegistry(),
        )
    group = await SurfaceGroupRepository(db_session).get(
        surface_id=surface_id, external_channel_id=GROUP
    )
    assert group is not None and group.invite_link is not None

    # Talk nobody put to the bot is logged and left alone.
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfaceDirectWebhookIngress(
            surface_id=surface_id,
            payload=_said_in_whatsapp_group("Thanks all, talk at 3", sender=CLIENT),
        )
    )
    await uow.commit()
    assert context is None
    lines = await SurfaceGroupRepository(db_session).recent_lines(group=group, limit=5)
    assert [line.text for line in lines] == ["Thanks all, talk at 3"]

    # A member who speaks to it by the pod's name is answered, as themselves.
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceDirectWebhookIngress(
            surface_id=surface_id,
            payload=_said_in_whatsapp_group(
                f"{test_pod['name']}, send Tiago our lead times", sender=MEMBER
            ),
        ),
        script=[script_text("Lead times are two weeks.")],
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.answers_outsider is False


async def test_an_own_telegram_bot_greets_once_and_answers_only_when_asked(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    _wire_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client, pod_id, config={"type": "TELEGRAM"}
    )
    surface_id = UUID(surface["id"])
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(MEMBER_TELEGRAM_ID),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    added = SurfacePlatformWebhookIngress(
        source="telegram",
        payload={
            "update_id": 5001,
            "my_chat_member": {
                "chat": {
                    "id": TELEGRAM_GROUP,
                    "type": "supergroup",
                    "title": "Launch crew",
                },
                "from": {"id": MEMBER_TELEGRAM_ID, "is_bot": False},
                "date": 1700000000,
                "old_chat_member": {"status": "left"},
                "new_chat_member": {"status": "member"},
            },
        },
        headers={},
    )
    for _ in range(2):  # Telegram may say it twice; the group hears hello once.
        assert await build_app_event_handler(
            SqlAlchemyUnitOfWork(db_session)
        ).try_handle_lifecycle(added)
        await db_session.commit()
    sent = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    hellos = [
        message
        for message in sent
        if "keeps what's said here" in message.get("text", "")
    ]
    assert len(hellos) == 1
    assert hellos[0]["chat_id"] == str(TELEGRAM_GROUP)

    uow = SqlAlchemyUnitOfWork(db_session)
    unaddressed = await build_surface_ingress(uow).prepare_ingress(
        SurfaceDirectWebhookIngress(
            surface_id=surface_id,
            payload=_said_in_telegram_group(
                "proofs went out", sender_id=STRANGER_TELEGRAM_ID, mention=False
            ),
        )
    )
    await uow.commit()
    assert unaddressed is None

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceDirectWebhookIngress(
            surface_id=surface_id,
            payload=_said_in_telegram_group(
                "@lemmabot when are proofs due?",
                sender_id=STRANGER_TELEGRAM_ID,
                mention=True,
            ),
        ),
        script=[script_text("Friday.")],
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.answers_outsider is True
    group = await SurfaceGroupRepository(db_session).get(
        surface_id=surface_id, external_channel_id=str(TELEGRAM_GROUP)
    )
    assert group is not None
    said = [
        line.text
        for line in await SurfaceGroupRepository(db_session).recent_lines(
            group=group, limit=10
        )
    ]
    assert "proofs went out" in said
