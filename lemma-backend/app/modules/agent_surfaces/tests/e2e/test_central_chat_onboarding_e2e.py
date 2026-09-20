"""Real auth and persistence, with platform delivery through the provider fixture."""

import json
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_replay import replay_onboarding
from app.modules.agent_surfaces.api.dependencies import (
    build_surface_event_handler_with_factory,
)
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
)
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent_surfaces.tests.e2e.helpers import _whatsapp_payload
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("native", [False, True])
async def test_shared_whatsapp_without_a_surface_provisions_and_replays(
    async_client, db_session, fake_whatsapp, message_store, monkeypatch, native
):
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-onboarding")
    if native:
        monkeypatch.setattr(
            surface_settings, "whatsapp_onboarding_email_flow_id", "email-flow"
        )
        monkeypatch.setattr(
            surface_settings, "whatsapp_onboarding_code_flow_id", "code-flow"
        )
    codes: list[str] = []

    async def capture(*, email: str, code: str) -> bool:
        codes.append(code)
        return True

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    coordinator = ChatOnboardingCoordinator(
        factory,
        challenges=EmailChallengeService(
            sessions, send_email=capture, enforce_send_limits=allow_test_delivery
        ),
    )
    sender = "15550" + str(int(uuid4().hex[:7], 16)).zfill(9)
    email = f"chat-{uuid4().hex}@gmail.com"

    async def say(text: str, *, form=False):
        payload = _whatsapp_payload(
            text=text,
            message_id=uuid4().hex,
            phone_number_id="1234567890",
            waba_id="waba-onboarding",
            sender_phone=sender,
        )
        if text == "Please check this invoice":
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message.pop("text")
            message["type"] = "document"
            message["document"] = {
                "id": "onboarding-invoice",
                "filename": "invoice.txt",
                "mime_type": "text/plain",
                "caption": text,
            }
        if form and native:
            token = message_store.get_all("WHATSAPP")[-1]["interactive"]["action"][
                "parameters"
            ]["flow_token"]
            message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            message.pop("text")
            message["type"] = "interactive"
            message["interactive"] = {
                "type": "nfm_reply",
                "nfm_reply": {
                    "response_json": json.dumps({"flow_token": token, "answer": text})
                },
            }
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="whatsapp", payload=payload)
        )

    assert (await say("Please check this invoice")).handled
    assert message_store.get_all("WHATSAPP")
    assert (await say(email, form=True)).handled
    assert len(codes) == 1
    assert (await say(codes[0], form=True)).handled
    async with sessions() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None and user.mobile_number == f"+{sender}"
        binding = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.user_id == user.id
            )
        )
        assert binding is not None and binding.revoked_at is None
        pending = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.user_id == user.id
            )
        )
        assert pending is not None and pending.ready_at is not None
        pending_id = pending.id
    queue = AsyncMock(spec=SharedStreaqJobQueue)
    queue.enqueue.side_effect = RuntimeError("queue temporarily unavailable")
    with pytest.raises(RuntimeError, match="queue temporarily unavailable"):
        await replay_onboarding(pending_id, uow_factory=factory, job_queue=queue)
    async with sessions() as session:
        pending = await session.get(PendingChatOnboarding, pending_id)
        assert pending.original_event is not None and pending.handed_off_at is None
    queue.enqueue.side_effect = None
    queue.reset_mock()
    await replay_onboarding(pending_id, uow_factory=factory, job_queue=queue)
    queue.enqueue.assert_awaited_once()
    context = queue.enqueue.call_args.kwargs["payload"]["context"]
    assert context["mode"] == "chat"
    assert context["message_text"] == "Please check this invoice"
    assert codes[0] not in str(context)
    assert context["event"]["is_dm"] is True
    parsed_context = SurfaceChatContext.model_validate(context)
    handler = build_surface_event_handler_with_factory(factory)
    with suppress_agent_run_enqueue():
        await handler.execute_chat(parsed_context)
        await handler.execute_chat(parsed_context)
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentRunModel)
                .where(AgentRunModel.conversation_id == parsed_context.conversation_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MessageModel)
                .where(
                    MessageModel.conversation_id == parsed_context.conversation_id,
                    MessageModel.role == "user",
                )
            )
            == 1
        )
    assert message_store.get_all("WHATSAPP_MEDIA_DOWNLOAD")
    await run_scripted_agent_run(
        db_session,
        conversation_id=parsed_context.conversation_id,
        user_id=parsed_context.user_id,
        pod_id=parsed_context.pod_id,
        agent_name=parsed_context.agent_name,
        script=[script_text("Your invoice is ready to review.")],
    )
    assert any(
        "Your invoice is ready to review." in str(item)
        for item in message_store.get_all("WHATSAPP")
    )
    await replay_onboarding(pending_id, uow_factory=factory, job_queue=queue)
    queue.enqueue.assert_awaited_once()
    assert not (await say("Thanks, now check the totals")).handled
    async with sessions() as session:
        pending = await session.get(PendingChatOnboarding, pending_id)
        assert pending.original_event is None and pending.handed_off_at is not None


async def test_telegram_requires_own_contact_without_a_username_or_existing_surface(
    async_client, db_session, fake_telegram, message_store, monkeypatch
):
    from app.modules.agent_surfaces.tests.e2e.helpers import _telegram_payload

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    codes = []

    async def capture(*, email, code):
        codes.append(code)
        return True

    coordinator = ChatOnboardingCoordinator(
        factory,
        challenges=EmailChallengeService(
            sessions, send_email=capture, enforce_send_limits=allow_test_delivery
        ),
    )
    actor = int(uuid4().hex[:8], 16)
    phone = "15550" + str(int(uuid4().hex[:7], 16)).zfill(9)
    email = f"telegram-{uuid4().hex}@gmail.com"

    async def say(text, contact_id=None):
        payload = _telegram_payload(
            text=text, message_id=int(uuid4().hex[:7], 16), sender_id=actor
        )
        payload["message"]["from"].pop("username")
        if contact_id is not None:
            payload["message"].pop("text")
            payload["message"]["contact"] = {
                "user_id": contact_id,
                "phone_number": phone,
                "first_name": "Sender",
            }
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="telegram", payload=payload)
        )

    assert (await say("Help plan my day")).handled
    assert (await say("+" + phone)).handled
    assert (await say("", actor + 1)).handled
    assert codes == []
    assert (await say("", actor)).handled
    assert (await say(email)).handled
    assert (await say(codes[0])).handled
    async with sessions() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user.mobile_number == "+" + phone
        binding = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.user_id == user.id
            )
        )
        assert binding.external_user_id == str(actor) and binding.revoked_at is None
        pending = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.user_id == user.id
            )
        )
        assert pending.ready_at is not None
        pending_id = pending.id
    queue = AsyncMock(spec=SharedStreaqJobQueue)
    await replay_onboarding(pending_id, uow_factory=factory, job_queue=queue)
    context = SurfaceChatContext.model_validate(
        queue.enqueue.call_args.kwargs["payload"]["context"]
    )
    assert context.message_text == "Help plan my day"
    with suppress_agent_run_enqueue():
        await build_surface_event_handler_with_factory(factory).execute_chat(context)
    await run_scripted_agent_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
        script=[script_text("Your day is planned")],
    )
    assert any(
        "Your day is planned" in str(item) for item in message_store.get_all("TELEGRAM")
    )


async def test_phone_replacement_revokes_old_binding_and_preserves_new_proof(
    authenticated_client, fixed_test_user, db_session, fake_whatsapp, monkeypatch
):
    from uuid import UUID
    from app.modules.agent_surfaces.events.handlers import on_identity_event
    from app.modules.identity.domain.events import UserMobileChangedEvent
    from app.modules.test_support.fakes import PassthroughEventInbox
    from datetime import datetime, timezone

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-onboarding")
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    user_id = UUID(fixed_test_user["id"])
    old_binding = uuid4().hex
    phone = "15550" + str(int(uuid4().hex[:7], 16)).zfill(9)
    async with sessions.begin() as session:
        user = await session.get(User, user_id)
        user.mobile_number = "+155500000001"
        user.mobile_verified_at = datetime.now(timezone.utc)
        email = user.email
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=old_binding,
                platform="TELEGRAM",
                tenant_id="",
                external_user_id="old-actor",
                user_id=user_id,
                verified_phone=user.mobile_number,
            )
        )
    codes = []

    async def capture(*, email, code):
        codes.append(code)
        return True

    coordinator = ChatOnboardingCoordinator(
        factory,
        challenges=EmailChallengeService(
            sessions, send_email=capture, enforce_send_limits=allow_test_delivery
        ),
    )

    async def say(text):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    phone_number_id="1234567890",
                    waba_id="waba-onboarding",
                    sender_phone=phone,
                ),
            )
        )

    assert (await say("Hello from my new number")).handled
    assert (await say(email)).handled
    assert (await say(codes[0])).handled
    await on_identity_event(
        UserMobileChangedEvent(user_id=user_id).model_dump(mode="json"),
        uow_factory=factory,
        inbox=PassthroughEventInbox(),
    )
    async with sessions() as session:
        user = await session.get(User, user_id)
        assert user.mobile_number == "+" + phone
        bindings = list(
            (
                await session.scalars(
                    select(VerifiedSurfaceIdentity).where(
                        VerifiedSurfaceIdentity.user_id == user_id
                    )
                )
            ).all()
        )
        assert (
            next(
                binding for binding in bindings if binding.binding_key == old_binding
            ).revoked_at
            is not None
        )
        current = next(
            binding for binding in bindings if binding.external_user_id == phone
        )
        assert (
            current.revoked_at is None and current.verified_phone == user.mobile_number
        )
