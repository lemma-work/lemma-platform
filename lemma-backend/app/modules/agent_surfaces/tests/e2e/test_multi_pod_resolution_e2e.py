"""E2E for deterministic surface selection when one person is reachable via a
shared system bot across pods in *different orgs* (WS3 / concern #1).

The shared Telegram system bot fans one inbound DM in to every active Telegram
surface. These tests prove the selection is deterministic and sticky:
- the first message routes to exactly one surface and creates one conversation;
- a follow-up in the same chat reuses that surface (continuity — no split-brain);
- a user-set default overrides the tiebreak.

Uses ``prepare_ingress`` directly (the routing decision) rather than a full agent
run — a DM's identity resolution makes no platform API call, so no fake bot is
needed."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_surface,
    _ensure_connector_account,
    _seed_external_user,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e

SYSTEM_WHATSAPP_PHONE_NUMBER_ID = "1234567890"
SYSTEM_WHATSAPP_WABA_ID = "waba-routing-system"
CUSTOM_WHATSAPP_PHONE_NUMBER_ID = "10987654321"
CUSTOM_WHATSAPP_WABA_ID = "waba-routing-custom"


async def _prepare_telegram_dm(db_session: AsyncSession, payload: dict):
    """Run just the ingress routing for a Telegram DM and persist its link."""
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload)
    )
    await uow.commit()
    return context


def _wire_shared_platform(monkeypatch, platform: str) -> None:
    if platform == "TELEGRAM":
        monkeypatch.setattr(surface_settings, "telegram_bot_token", "shared-native-bot")
        monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
        return
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(
        surface_settings, "whatsapp_phone_number_id", SYSTEM_WHATSAPP_PHONE_NUMBER_ID
    )
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", SYSTEM_WHATSAPP_WABA_ID)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")


def _external_id(platform: str, suffix: int) -> str:
    if platform == "TELEGRAM":
        return str(910000000 + suffix)
    return f"1555{suffix:07d}"


def _thread_id(platform: str, external_id: str, *, phone_number_id: str | None = None) -> str:
    if platform == "TELEGRAM":
        return external_id
    return f"{external_id}@{phone_number_id or SYSTEM_WHATSAPP_PHONE_NUMBER_ID}"


def _dm_payload(
    platform: str,
    *,
    external_id: str,
    text: str,
    message_id: int,
    phone_number_id: str | None = None,
    waba_id: str | None = None,
) -> dict:
    if platform == "TELEGRAM":
        return _telegram_payload(
            text=text,
            message_id=message_id,
            sender_id=int(external_id),
        )
    return _whatsapp_payload(
        text=text,
        message_id=f"wamid-routing-{message_id}-{external_id}",
        phone_number_id=phone_number_id or SYSTEM_WHATSAPP_PHONE_NUMBER_ID,
        waba_id=waba_id or SYSTEM_WHATSAPP_WABA_ID,
        sender_phone=external_id,
    )


async def _prepare_platform_dm(
    db_session: AsyncSession,
    platform: str,
    payload: dict,
    *,
    receiver_surface_ids: list[UUID] | None = None,
):
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source=platform.lower(),
            payload=payload,
            receiver_surface_ids=receiver_surface_ids,
        )
    )
    await uow.commit()
    return context


async def _seed_platform_user(
    db_session: AsyncSession,
    *,
    platform: str,
    external_id: str,
    user_id: str,
    tenant_id: str | None = None,
) -> None:
    await _seed_external_user(
        db_session,
        platform=platform,
        external_user_id=external_id,
        resolved_user_id=UUID(user_id),
        tenant_id=tenant_id if platform == "WHATSAPP" else None,
    )


async def _two_orgs_with_system_surfaces(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    owner_user: dict,
    *,
    platform: str,
):
    org_a = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_a.create_org_with_pod(name_prefix=f"{platform}MultiPodA")
    org_b = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_b.create_org_with_pod(name_prefix=f"{platform}MultiPodB")

    surface_a = await _create_surface(
        authenticated_client, org_a.pod_id, config={"type": platform}
    )
    surface_b = await _create_surface(
        authenticated_client, org_b.pod_id, config={"type": platform}
    )
    return org_a, org_b, surface_a, surface_b


async def _set_user_default_surface(
    async_client: AsyncClient,
    *,
    user: dict,
    platform: str,
    surface_id: str,
) -> None:
    put = await async_client.put(
        "/surfaces/me/default",
        json={"platform": platform, "surface_id": surface_id},
        headers=auth_headers(user),
    )
    assert put.status_code == 200, put.text


async def _two_orgs_with_telegram_surfaces(
    authenticated_client: AsyncClient, async_client: AsyncClient, owner_user: dict
):
    org_a = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_a.create_org_with_pod(name_prefix="MultiPodA")
    org_b = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_b.create_org_with_pod(name_prefix="MultiPodB")

    surface_a = await _create_surface(
        authenticated_client, org_a.pod_id, config={"type": "TELEGRAM"}
    )
    surface_b = await _create_surface(
        authenticated_client, org_b.pod_id, config={"type": "TELEGRAM"}
    )
    return org_a, org_b, surface_a, surface_b


async def test_shared_bot_multi_org_routing_is_deterministic_and_sticky(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "shared-native-bot")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)

    org_a, org_b, surface_a, surface_b = await _two_orgs_with_telegram_surfaces(
        authenticated_client, async_client, fixed_test_user
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="900900900",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # First DM → routes to exactly one of the two surfaces (deterministic).
    ctx1 = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="hi", message_id=1, sender_id=900900900)
    )
    assert isinstance(ctx1, SurfaceChatContext)
    assert str(ctx1.surface_id) in {surface_a["id"], surface_b["id"]}

    # Second DM in the same chat → continuity keeps it on the SAME surface and
    # the SAME conversation (no bounce, no split-brain).
    ctx2 = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="again", message_id=2, sender_id=900900900)
    )
    assert ctx2.surface_id == ctx1.surface_id
    assert ctx2.conversation_id == ctx1.conversation_id

    # Exactly one conversation exists — the other org's pod has none.
    chosen_pod = str(ctx1.pod_id)
    other_pod = org_b.pod_id if chosen_pod == org_a.pod_id else org_a.pod_id
    here = await _conversation_by_external_thread(
        authenticated_client, pod_id=chosen_pod, external_thread_id="900900900"
    )
    assert here is not None
    there = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=other_pod,
        external_thread_id="900900900",
        timeout_seconds=1.0,
    )
    assert there is None


async def test_shared_bot_routes_to_user_default_surface(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "shared-native-bot")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)

    org_a, org_b, surface_a, surface_b = await _two_orgs_with_telegram_surfaces(
        authenticated_client, async_client, fixed_test_user
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="911911911",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # The user picks org B's surface as their Telegram default.
    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "TELEGRAM", "surface_id": surface_b["id"]},
    )
    assert put.status_code == 200, put.text

    # First DM (no prior conversation) → the default wins over the tiebreak.
    ctx = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="hi", message_id=10, sender_id=911911911)
    )
    assert isinstance(ctx, SurfaceChatContext)
    assert ctx.surface_id == UUID(surface_b["id"])
    assert str(ctx.pod_id) == org_b.pod_id


@pytest.mark.parametrize("platform", ["TELEGRAM", "WHATSAPP"])
async def test_shared_system_bot_multi_user_routing_matrix(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
    platform: str,
):
    """Five signed-up users per platform exercise the shared system-bot matrix.

    Parameterized over Telegram and WhatsApp, this gives the 10-user shared
    surface scenario: single-pod member, default, deterministic tiebreak,
    continuity, duplicate delivery, and signed-up non-member no-leak behavior.
    """
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    _wire_shared_platform(monkeypatch, platform)
    org_a, org_b, surface_a, surface_b = await _two_orgs_with_system_surfaces(
        authenticated_client,
        async_client,
        fixed_test_user,
        platform=platform,
    )
    tenant_id = SYSTEM_WHATSAPP_WABA_ID if platform == "WHATSAPP" else None

    single_user = await org_a.create_user(f"{platform.lower()}-single")
    await org_a.add_user_to_pod(user=single_user, role="POD_EDITOR")
    single_external = _external_id(platform, 1)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=single_external,
        user_id=single_user["id"],
        tenant_id=tenant_id,
    )
    single_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=single_external,
            text="single pod",
            message_id=101,
        ),
    )
    assert isinstance(single_ctx, SurfaceChatContext)
    assert single_ctx.surface_id == UUID(surface_a["id"])
    assert str(single_ctx.pod_id) == org_a.pod_id

    default_user = await org_a.create_user(f"{platform.lower()}-default")
    await org_a.add_user_to_pod(user=default_user, role="POD_EDITOR")
    await org_b.add_user_to_pod(user=default_user, role="POD_EDITOR")
    await _set_user_default_surface(
        async_client,
        user=default_user,
        platform=platform,
        surface_id=surface_b["id"],
    )
    default_external = _external_id(platform, 2)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=default_external,
        user_id=default_user["id"],
        tenant_id=tenant_id,
    )
    default_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=default_external,
            text="use my default",
            message_id=102,
        ),
    )
    assert isinstance(default_ctx, SurfaceChatContext)
    assert default_ctx.surface_id == UUID(surface_b["id"])
    assert str(default_ctx.pod_id) == org_b.pod_id

    tiebreak_user = await org_a.create_user(f"{platform.lower()}-tiebreak")
    await org_a.add_user_to_pod(user=tiebreak_user, role="POD_EDITOR")
    await org_b.add_user_to_pod(user=tiebreak_user, role="POD_EDITOR")
    tiebreak_external = _external_id(platform, 3)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=tiebreak_external,
        user_id=tiebreak_user["id"],
        tenant_id=tenant_id,
    )
    tiebreak_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=tiebreak_external,
            text="no default set",
            message_id=103,
        ),
    )
    assert isinstance(tiebreak_ctx, SurfaceChatContext)
    assert tiebreak_ctx.surface_id == UUID(surface_a["id"])
    assert str(tiebreak_ctx.pod_id) == org_a.pod_id

    continuity_user = await org_a.create_user(f"{platform.lower()}-continuity")
    await org_a.add_user_to_pod(user=continuity_user, role="POD_EDITOR")
    await org_b.add_user_to_pod(user=continuity_user, role="POD_EDITOR")
    continuity_external = _external_id(platform, 4)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=continuity_external,
        user_id=continuity_user["id"],
        tenant_id=tenant_id,
    )
    continuity_first = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=continuity_external,
            text="start a thread",
            message_id=104,
        ),
    )
    assert isinstance(continuity_first, SurfaceChatContext)
    opposite_surface = (
        surface_b
        if continuity_first.surface_id == UUID(surface_a["id"])
        else surface_a
    )
    # With NO default set, a follow-up message continues on the same surface and
    # conversation (continuity holds).
    continuity_second = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=continuity_external,
            text="same thread wins",
            message_id=105,
        ),
    )
    assert isinstance(continuity_second, SurfaceChatContext)
    assert continuity_second.surface_id == continuity_first.surface_id
    assert continuity_second.conversation_id == continuity_first.conversation_id

    # Now the user picks a default on the OTHER pod. A valid default is
    # authoritative — it overrides continuity, re-routing new messages to the
    # chosen surface and starting a fresh conversation there (issue 3).
    await _set_user_default_surface(
        async_client,
        user=continuity_user,
        platform=platform,
        surface_id=opposite_surface["id"],
    )
    continuity_third = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=continuity_external,
            text="default now wins",
            message_id=108,
        ),
    )
    assert isinstance(continuity_third, SurfaceChatContext)
    assert continuity_third.surface_id == UUID(opposite_surface["id"])
    assert continuity_third.conversation_id != continuity_first.conversation_id

    duplicate_user = await org_a.create_user(f"{platform.lower()}-duplicate")
    await org_a.add_user_to_pod(user=duplicate_user, role="POD_EDITOR")
    duplicate_external = _external_id(platform, 5)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=duplicate_external,
        user_id=duplicate_user["id"],
        tenant_id=tenant_id,
    )
    duplicate_payload = _dm_payload(
        platform,
        external_id=duplicate_external,
        text="deliver once",
        message_id=106,
    )
    duplicate_first = await _prepare_platform_dm(
        db_session, platform, duplicate_payload
    )
    duplicate_second = await _prepare_platform_dm(
        db_session, platform, duplicate_payload
    )
    assert isinstance(duplicate_first, SurfaceChatContext)
    assert duplicate_second is None

    non_member = await org_a.create_user(f"{platform.lower()}-nonmember")
    non_member_external = _external_id(platform, 6)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=non_member_external,
        user_id=non_member["id"],
        tenant_id=tenant_id,
    )
    non_member_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=non_member_external,
            text="where do I go?",
            message_id=107,
        ),
    )
    if non_member_ctx is not None:
        assert isinstance(non_member_ctx, SurfaceReplyContext)
        message = non_member_ctx.reply_message or ""
        assert f"/pods/{org_a.pod_id}" not in message
        assert f"/pods/{org_b.pod_id}" not in message


@pytest.mark.parametrize("platform", ["TELEGRAM", "WHATSAPP"])
async def test_custom_bot_scope_and_system_bot_threads_do_not_cross(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
    platform: str,
):
    from app.core.config import settings as app_settings
    from app.modules.test_support.e2e_authz import signup_user

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    _wire_shared_platform(monkeypatch, platform)

    pod_id = test_pod["id"]
    system_surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": platform},
        name=f"{platform.lower()}-system",
    )
    connector_id = platform.lower()
    credentials = (
        {"bot_token": f"custom-{platform.lower()}-bot"}
        if platform == "TELEGRAM"
        else {
            "access_token": "custom-wa-token",
            "phone_number_id": CUSTOM_WHATSAPP_PHONE_NUMBER_ID,
            "waba_id": CUSTOM_WHATSAPP_WABA_ID,
        }
    )
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id=connector_id,
        credentials=credentials,
    )
    custom_surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={
            "type": platform,
            "account_id": str(account.id),
            "credential_mode": "CUSTOM",
        },
        name=f"{platform.lower()}-custom",
    )
    custom_surface_id = UUID(custom_surface["id"])

    member_external = _external_id(platform, 701)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=member_external,
        user_id=fixed_test_user["id"],
        tenant_id=CUSTOM_WHATSAPP_WABA_ID if platform == "WHATSAPP" else None,
    )
    if platform == "WHATSAPP":
        await _seed_platform_user(
            db_session,
            platform=platform,
            external_id=member_external,
            user_id=fixed_test_user["id"],
            tenant_id=SYSTEM_WHATSAPP_WABA_ID,
        )

    custom_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=member_external,
            text="custom bot thread",
            message_id=701,
            phone_number_id=CUSTOM_WHATSAPP_PHONE_NUMBER_ID,
            waba_id=CUSTOM_WHATSAPP_WABA_ID,
        ),
        receiver_surface_ids=[custom_surface_id],
    )
    assert isinstance(custom_ctx, SurfaceChatContext)
    assert custom_ctx.surface_id == custom_surface_id

    system_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=member_external,
            text="system bot thread",
            message_id=702,
        ),
    )
    assert isinstance(system_ctx, SurfaceChatContext)
    assert system_ctx.surface_id == UUID(system_surface["id"])
    assert system_ctx.conversation_id != custom_ctx.conversation_id

    outsider = await signup_user(async_client, f"{platform.lower()}-custom-outsider")
    outsider_external = _external_id(platform, 702)
    await _seed_platform_user(
        db_session,
        platform=platform,
        external_id=outsider_external,
        user_id=outsider["id"],
        tenant_id=CUSTOM_WHATSAPP_WABA_ID if platform == "WHATSAPP" else None,
    )
    non_member_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=outsider_external,
            text="custom access?",
            message_id=703,
            phone_number_id=CUSTOM_WHATSAPP_PHONE_NUMBER_ID,
            waba_id=CUSTOM_WHATSAPP_WABA_ID,
        ),
        receiver_surface_ids=[custom_surface_id],
    )
    assert isinstance(non_member_ctx, SurfaceReplyContext)
    assert f"/pods/{pod_id}" in (non_member_ctx.reply_message or "")

    unknown_external = _external_id(platform, 703)
    unresolved_ctx = await _prepare_platform_dm(
        db_session,
        platform,
        _dm_payload(
            platform,
            external_id=unknown_external,
            text="new phone who dis",
            message_id=704,
            phone_number_id=CUSTOM_WHATSAPP_PHONE_NUMBER_ID,
            waba_id=CUSTOM_WHATSAPP_WABA_ID,
        ),
        receiver_surface_ids=[custom_surface_id],
    )
    assert isinstance(unresolved_ctx, SurfaceReplyContext)
    unresolved_message = (unresolved_ctx.reply_message or "").lower()
    assert (
        "sign up" in unresolved_message
        or "contact" in unresolved_message
        or "share your phone" in unresolved_message
    )
    assert "/pods/" not in (unresolved_ctx.reply_message or "")
