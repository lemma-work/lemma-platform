"""Linking a Telegram chat to the signed-in user, with no email anywhere.

The Desktop shape end to end: the shared Telegram bot, an installation that
cannot send mail, and a signed-in user who mints a link in the app and presses
Start in Telegram. Real auth, real persistence, the real coordinator and the
real ingestion path; Telegram is the fake Bot API server.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.composition import build_surface_turn_starter
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import (
    IdentityProof,
    OnboardingStep,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_replay import replay_onboarding
from app.modules.agent_surfaces.services.telegram_link_tokens import (
    START_PAYLOAD_PREFIX,
)
from app.modules.agent_surfaces.tests.e2e.helpers import _telegram_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
def desktop_telegram(db_session, fake_telegram, message_store, monkeypatch):
    """The shared bot on an installation with no mail, and a chat to drive it.

    Returns `(say, sessions, factory, texts)`: `say(text, actor)` delivers one
    private message from Telegram user `actor` through the coordinator, and
    `texts()` is everything the bot has said so far.
    """
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    coordinator = ChatOnboardingCoordinator(factory, email_deliverable=lambda: False)

    def payload(text: str, actor: int) -> dict:
        return _telegram_payload(
            text=text, message_id=int(uuid4().hex[:7], 16), sender_id=actor
        )

    async def say(text: str, actor: int):
        request = SurfacePlatformWebhookIngress(
            source="telegram", payload=payload(text, actor)
        )
        return await coordinator.handle(request), request

    def texts() -> list[str]:
        return [
            str(item.get("text") or item) for item in message_store.get_all("TELEGRAM")
        ]

    return say, sessions, factory, texts


def _token(url: str) -> str:
    payload = parse_qs(urlparse(url).query)["start"][0]
    assert payload.startswith(START_PAYLOAD_PREFIX)
    return payload.removeprefix(START_PAYLOAD_PREFIX)


def _actor() -> int:
    return int(uuid4().hex[:8], 16)


async def test_a_minted_link_connects_a_new_chat_and_its_next_message_reaches_the_pod(
    desktop_telegram, authenticated_client, fixed_test_user, test_pod, db_session
) -> None:
    say, sessions, factory, texts = desktop_telegram
    pod_id = test_pod["id"]

    options = await authenticated_client.get("/surfaces/me/telegram-link")
    assert options.status_code == 200, options.text
    assert options.json()["bot_username"] == "lemmabot"
    assert pod_id in [pod["id"] for pod in options.json()["pods"]]

    minted = await authenticated_client.post(
        "/surfaces/me/telegram-link", json={"pod_id": pod_id}
    )
    assert minted.status_code == 200, minted.text
    link = minted.json()
    assert link["url"].startswith("https://t.me/lemmabot?start=link_")
    assert link["pod_id"] == pod_id

    actor = _actor()
    result, _ = await say(f"/start link_{_token(link['url'])}", actor)
    assert result.handled

    # Told whose account this is, and never asked for a phone or an email.
    assert any(f"linked to {fixed_test_user['email']}" in text for text in texts())
    assert not any("email address" in text.lower() for text in texts())

    async with sessions() as session:
        identity = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.external_user_id == str(actor)
            )
        )
        assert identity is not None
        assert identity.user_id == UUID(fixed_test_user["id"])
        assert identity.proof == IdentityProof.LINK_TOKEN
        assert identity.verified_phone is None and identity.revoked_at is None
        pending = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.user_id == UUID(fixed_test_user["id"])
            )
        )
        assert pending.step == OnboardingStep.READY and pending.ready_at is not None
        # The spent token is not kept anywhere on the row.
        assert "link_" not in str(pending.original_event)
        assert "link_" not in str(pending.destination)
        pending_id = pending.id

    # The replay is a bare `/start`, which the agent answers with its greeting.
    queue = AsyncMock(spec=SharedStreaqJobQueue)
    await replay_onboarding(pending_id, uow_factory=factory, job_queue=queue)
    replayed = SurfaceChatContext.model_validate(
        queue.enqueue.call_args.kwargs["payload"]["context"]
    )
    assert replayed.message_text == "/start"
    assert str(replayed.pod_id) == pod_id
    with suppress_agent_run_enqueue():
        await build_surface_turn_starter(factory).execute_chat(replayed)
    assert any(text.startswith("Hi") for text in texts())

    # The next message is ordinary traffic: onboarding lets it through, and
    # ingestion recognises the sender without a phone and routes it to the pod.
    result, request = await say("Plan my day", actor)
    assert not result.handled
    context = await process_ingress_and_run_scripted(
        db_session, request, script=[script_text("Your day is planned")]
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.user_id == UUID(fixed_test_user["id"])
    assert str(context.pod_id) == pod_id
    assert any("Your day is planned" in text for text in texts())


async def test_a_link_works_once_and_a_spent_one_says_where_to_get_another(
    desktop_telegram, authenticated_client, test_pod
) -> None:
    say, sessions, _, texts = desktop_telegram
    minted = await authenticated_client.post(
        "/surfaces/me/telegram-link", json={"pod_id": test_pod["id"]}
    )
    token = _token(minted.json()["url"])

    await say(f"/start link_{token}", _actor())
    stranger = _actor()
    result, _ = await say(f"/start link_{token}", stranger)

    assert result.handled
    assert "expired or was already used" in texts()[-1]
    assert "Server setup" in texts()[-1]
    async with sessions() as session:
        assert (
            await session.scalar(
                select(VerifiedSurfaceIdentity).where(
                    VerifiedSurfaceIdentity.external_user_id == str(stranger)
                )
            )
            is None
        )


async def test_pressing_the_button_again_moves_a_linked_chat_to_another_pod(
    desktop_telegram, authenticated_client, fixed_test_org, test_pod
) -> None:
    say, sessions, _, texts = desktop_telegram
    other = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Second Pod {uuid4()}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert other.status_code == 201, other.text
    actor = _actor()

    for pod in (test_pod, other.json()):
        minted = await authenticated_client.post(
            "/surfaces/me/telegram-link", json={"pod_id": pod["id"]}
        )
        await say(f"/start link_{_token(minted.json()['url'])}", actor)
        assert f"answers from {pod['name']}" in texts()[-1]

    listed = await authenticated_client.get("/surfaces/me")
    telegram = next(g for g in listed.json()["groups"] if g["platform"] == "TELEGRAM")
    default = next(s for s in telegram["surfaces"] if s["is_default"])
    assert default["pod_id"] == other.json()["id"]


async def test_a_stranger_is_not_asked_for_an_email_that_can_never_arrive(
    desktop_telegram,
) -> None:
    say, sessions, _, texts = desktop_telegram
    actor = _actor()

    result, _ = await say("hello?", actor)

    assert result.handled
    assert "private Lemma" in texts()[-1]
    assert not any("email address" in text.lower() for text in texts())
    async with sessions() as session:
        pending = await session.scalar(
            select(PendingChatOnboarding).order_by(
                PendingChatOnboarding.created_at.desc()
            )
        )
        assert pending.step == OnboardingStep.REFUSED
        assert pending.handed_off_at is not None
        assert pending.original_event is None


async def test_the_link_api_refuses_what_it_cannot_honour(
    authenticated_client, test_pod, fake_telegram, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    monkeypatch.setattr(surface_settings, "telegram_bot_token", None)
    no_bot = await authenticated_client.post("/surfaces/me/telegram-link", json={})
    assert no_bot.status_code == 409, no_bot.text
    assert no_bot.json()["code"] == "TELEGRAM_SYSTEM_BOT_UNAVAILABLE"

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    not_mine = await authenticated_client.post(
        "/surfaces/me/telegram-link", json={"pod_id": str(uuid4())}
    )
    assert not_mine.status_code == 403, not_mine.text


async def test_the_link_api_needs_a_signed_in_user(async_client) -> None:
    response = await async_client.post("/surfaces/me/telegram-link", json={})
    assert response.status_code == 401, response.text
