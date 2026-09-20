"""Someone the system recognises, with nowhere yet to talk.

A verified identity and nowhere for the message to go used to fall through to
ordinary ingestion, which answered by telling a person who already has an
account to go and open the website. These drive the step that replaced that:
the offer, the answer, and the two ways an answer can be wrong.

The premise is deliberately the **shared bot**. On a company installation,
having a transport *is* having a candidate -- the surface the message arrived
through is the one routing would pick -- so "recognised, nothing to route to"
is not a state that installation can reach for one of its own members. The
shared number can: it carries the message without belonging to any workspace,
so a recognised sender with no system-credential surface they can reach has
genuinely nowhere to go. The last test here pins the other half, that somebody
routing *can* place is never interrupted by the question.

The binding key is read back from the row the first message creates rather than
computed here. It is a hash of platform, tenant, installation and actor, and a
test that recomputed it would keep passing after the real one changed shape.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _whatsapp_payload,
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_WABA_ID = "waba-workspace-choice"
_PHONE_NUMBER_ID = "1234567890"


async def _swallow(*, email: str, code: str) -> bool:
    del email, code
    return True


def _coordinator(db_session):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    return sessions, ChatOnboardingCoordinator(
        SessionUnitOfWorkFactory(sessions),
        challenges=EmailChallengeService(
            sessions, send_email=_swallow, enforce_send_limits=allow_test_delivery
        ),
    )


@pytest.fixture
async def recognised_sender(
    db_session, test_pod, fixed_test_user, fake_whatsapp, monkeypatch
):
    """A shared-bot sender the system knows, with a pod and nowhere to talk.

    `test_pod` is the point: they are not stranded for want of a workspace,
    they are stranded for want of a surface in one. Built by letting the first
    message open signup for real and then keeping only its binding key -- that
    key is the one production computes, so this cannot drift away from it the
    way a hand-rolled hash would.
    """
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", _PHONE_NUMBER_ID)
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", _WABA_ID)

    sessions, coordinator = _coordinator(db_session)
    sender_phone = "1555" + str(uuid4().int)[:7]

    async def say(text: str):
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(
                source="whatsapp",
                payload=_whatsapp_payload(
                    text=text,
                    message_id=uuid4().hex,
                    phone_number_id=_PHONE_NUMBER_ID,
                    waba_id=_WABA_ID,
                    sender_phone=sender_phone,
                ),
            )
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None, "the first message did not open signup"
        binding_key = pending.binding_key
        await session.delete(pending)
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="WHATSAPP",
                tenant_id=_WABA_ID,
                external_user_id=sender_phone,
                user_id=fixed_test_user["id"],
            )
        )
        await session.commit()
    return say, sessions, binding_key


async def _shared_surfaces(sessions, pod_id) -> list[AgentSurface]:
    async with sessions() as session:
        rows = await session.scalars(
            select(AgentSurface).where(
                AgentSurface.pod_id == pod_id,
                AgentSurface.surface_type == "WHATSAPP",
            )
        )
        return list(rows)


async def test_a_recognised_sender_is_asked_which_workspace(recognised_sender) -> None:
    say, sessions, binding_key = recognised_sender

    result = await say("can you summarise my week")

    assert result.handled, "the message was left for ordinary ingestion"
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert parked is not None
        assert parked.step == OnboardingStep.AWAITING_POD
        # The offer has to be recorded, because the answer is read against it.
        assert parked.offered_pods, "nothing was offered, so no reply can be read"
        # And the message that got them here is kept, or answering costs them it.
        assert parked.original_event is not None


async def test_choosing_a_workspace_wires_the_conversation_to_it(
    recognised_sender,
) -> None:
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("1")

    chosen_pod_id = offered[0]["id"]
    surfaces = await _shared_surfaces(sessions, chosen_pod_id)
    assert len(surfaces) == 1, "choosing a workspace left nowhere to talk"
    assert surfaces[0].status == "ACTIVE"
    # System credentials, not the pod's own: shared routing considers only
    # those, so a surface on anything else is a destination nothing reaches.
    assert surfaces[0].credential_mode == "SYSTEM"
    async with sessions() as session:
        done = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert done.step == OnboardingStep.READY
        assert done.ready_at is not None, "the original message will never replay"


async def test_an_unreadable_answer_asks_again_rather_than_guessing(
    recognised_sender,
) -> None:
    """The one outcome worth more than convenience: never guess a workspace."""
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("whatever you think is best")

    async with sessions() as session:
        still_asking = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert still_asking.step == OnboardingStep.AWAITING_POD
    assert not await _shared_surfaces(sessions, offered[0]["id"]), (
        "an unreadable answer attached a workspace anyway"
    )


async def test_a_number_nobody_was_offered_attaches_nothing(
    recognised_sender,
) -> None:
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        offered = list(parked.offered_pods)

    await say("97")

    async with sessions() as session:
        still_asking = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert still_asking.step == OnboardingStep.AWAITING_POD
    assert not await _shared_surfaces(sessions, offered[0]["id"])


async def test_a_sender_routing_can_already_place_is_not_interrupted(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
) -> None:
    """The other half, and the more dangerous one to get wrong.

    This question is asked before ordinary ingestion, so asking it of somebody
    ingestion could have routed parks a working conversation behind a prompt
    they never needed to answer. The check is the routing resolver's own, so
    what ingestion would do and what this predicts cannot drift apart.
    """
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-workspace-choice",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "team_id": "T0123456",
                "bot_user_id": "U0AGSSTQZLH",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    sessions, coordinator = _coordinator(db_session)
    actor = "U" + uuid4().hex[:10]

    async def say(text: str):
        payload = _load_slack_dm_fixture(text=text, ts=uuid4().hex)
        payload["event"].update(
            {"user": actor, "channel": f"D{actor}", "channel_type": "im"}
        )
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="slack", payload=payload)
        )

    await say("hello")
    async with sessions() as session:
        pending = await session.scalar(select(PendingChatOnboarding))
        assert pending is not None
        binding_key = pending.binding_key
        await session.delete(pending)
        session.add(
            VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform="SLACK",
                tenant_id="T0123456",
                external_user_id=actor,
                user_id=fixed_test_user["id"],
            )
        )
        await session.commit()

    result = await say("can you summarise my week")

    assert not result.handled, "a routable sender was parked on a question"
    async with sessions() as session:
        parked = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert parked is None, "a routable sender was asked which workspace"
