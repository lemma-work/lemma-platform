"""Someone the system recognises, with nowhere yet to talk.

A verified identity and no personal route used to fall through to ordinary
ingestion, which answered by telling a person who already has an account to go
and open the website. These drive the step that replaced that: the offer, the
answer, and the two ways an answer can be wrong.

The binding key is read back from the row the first message creates rather than
computed here. It is a hash of platform, tenant, installation and actor, and a
test that recomputed it would keep passing after the real one changed shape.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.onboarding_state import OnboardingStep
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
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.fixture
async def recognised_sender(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """A Slack DM sender the system knows, with a pod and no personal route.

    Built by letting the first message open signup for real and then keeping
    only its binding key: that key is the one production computes, so this
    cannot drift away from it the way a hand-rolled hash would.
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
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    factory = SessionUnitOfWorkFactory(sessions)
    coordinator = ChatOnboardingCoordinator(
        factory,
        challenges=EmailChallengeService(
            sessions, send_email=_swallow, enforce_send_limits=allow_test_delivery
        ),
    )
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
        assert pending is not None, "the first message did not open signup"
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
    return say, sessions, binding_key


async def _swallow(*, email: str, code: str) -> bool:
    del email, code
    return True


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

    async with sessions() as session:
        route = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key,
                VerifiedSurfaceIdentity.pod_id.is_not(None),
            )
        )
        assert route is not None, "choosing a workspace left nowhere to talk"
        assert str(route.pod_id) == offered[0]["id"]
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

    await say("whatever you think is best")

    async with sessions() as session:
        still_asking = await session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )
        assert still_asking.step == OnboardingStep.AWAITING_POD
        route = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key,
                VerifiedSurfaceIdentity.pod_id.is_not(None),
            )
        )
        assert route is None, "an unreadable answer attached a workspace anyway"


async def test_a_number_nobody_was_offered_attaches_nothing(
    recognised_sender,
) -> None:
    say, sessions, binding_key = recognised_sender
    await say("can you summarise my week")

    await say("97")

    async with sessions() as session:
        route = await session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.binding_key == binding_key,
                VerifiedSurfaceIdentity.pod_id.is_not(None),
            )
        )
        assert route is None
