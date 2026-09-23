"""Company signup stays private and waits for access to the installation org."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import SharedStreaqJobQueue
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import _context_for_delivery
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.services.onboarding_replay import replay_onboarding
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("initial_dm_refused", [False, True])
async def test_channel_signup_waits_for_admin_and_resumes_in_installation_org(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
    initial_dm_refused,
):
    email = f"colleague-{uuid4().hex}@gmail.com"
    fake_slack._test_user_email = email
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-onboarding",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "team_id": "T0123456",
                "bot_user_id": "U0AGSSTQZLH",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    _, surface = await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    organization = await db_session.get(Organization, account.organization_id)
    organization.join_policy = "INVITE_ONLY"
    organization.email_domain = None
    await db_session.commit()
    codes = []

    async def capture(*, email, code):
        codes.append(code)
        return True

    factory = SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    coordinator = ChatOnboardingCoordinator(
        factory,
        challenges=EmailChallengeService(
            async_sessionmaker(db_session.bind, expire_on_commit=False),
            send_email=capture,
            enforce_send_limits=allow_test_delivery,
        ),
    )
    actor = "U" + uuid4().hex[:10]

    def delivery(text, *, channel=False, ts=None):
        payload = _load_slack_dm_fixture(text=text, ts=ts or uuid4().hex)
        payload["event"].update(
            {
                "user": actor,
                "channel": "Ccompany" if channel else f"D{actor}",
                "channel_type": "channel" if channel else "im",
            }
        )
        if channel:
            payload["event"]["type"] = "app_mention"
            payload["event"]["text"] = f"<@U0AGSSTQZLH> {text}"
        return SurfacePlatformWebhookIngress(source="slack", payload=payload)

    async def say(text, *, channel=False, ts=None):
        return await coordinator.handle(delivery(text, channel=channel, ts=ts))

    if initial_dm_refused:
        # A workspace whose app was installed without `im:write`. Slack answers
        # `conversations.open` with `missing_scope` and the SDK raises.
        #
        # This used to assert that `SlackApiError` came straight back out, and
        # that expectation was the bug written down. Nothing above the
        # coordinator catches that type: it left the subscriber, so the rest of
        # the delivery never ran and the inbox retried a message that could
        # never succeed -- on every message that person would ever send,
        # because a missing scope does not heal. `_context_for_delivery` is
        # where the difference shows, because only `PrivateDeliveryUnavailable`
        # reaches its fall-through, so that is what this drives now.
        #
        # What comes back is still None, and honestly so: ordinary ingestion
        # declines an unrecognised sender in a *channel* (selection finds no
        # surface for a sender it cannot resolve, and the unrouted fallback is
        # DM-only), so the "please sign up" reply the fall-through exists for is
        # not reachable from here either way. The fix buys the other half, and
        # it is the half that was costing a person every message they sent:
        # the refusal is answered instead of thrown, ingestion gets its turn,
        # and the delivery is acknowledged rather than retried forever.
        fake_slack.conversations_open_error = "missing_scope"
        refused = await _context_for_delivery(
            delivery("Help with my forecast", channel=True),
            onboarding_handler=coordinator.handle,
            uow_factory=factory,
        )
        assert refused is None
        assert message_store.get_all("SLACK") == []
        assert codes == []
        async with factory() as uow:
            pending = await uow.session.scalar(
                select(PendingChatOnboarding).where(
                    PendingChatOnboarding.installation_surface_id == UUID(surface["id"])
                )
            )
            assert pending.step == "handoff"
            assert pending.original_event is not None
        fake_slack.conversations_open_error = None
        assert (await say("Continue setup here")).handled
    else:
        assert (await say("Help with my forecast", channel=True)).handled
    assert (await say(email)).handled
    assert len(codes) == 1
    assert (await say(codes[0])).handled
    async with factory() as uow:
        user = await uow.session.scalar(select(User).where(User.email == email))
        assert user is not None
        pending = await uow.session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.user_id == user.id
            )
        )
        assert pending.step == "organization_access_required"
        assert (
            await uow.session.scalar(
                select(VerifiedSurfaceIdentity).where(
                    VerifiedSurfaceIdentity.user_id == user.id,
                    VerifiedSurfaceIdentity.pod_id.is_not(None),
                )
            )
            is None
        )
        assert (
            await uow.session.scalar(
                select(OrganizationMember).where(OrganizationMember.user_id == user.id)
            )
            is None
        )
        uow.session.add(
            OrganizationMember(
                user_id=user.id,
                organization_id=account.organization_id,
                role="ORG_MEMBER",
            )
        )
        user_id = user.id
    assert (await say("My admin added me")).handled
    assert len(codes) == 1
    async with factory() as uow:
        route = await uow.session.scalar(
            select(VerifiedSurfaceIdentity).where(
                VerifiedSurfaceIdentity.user_id == user_id,
                VerifiedSurfaceIdentity.pod_id.is_not(None),
            )
        )
        assert route is not None and route.installation_surface_id == UUID(
            surface["id"]
        )
        assert route.pod_id != UUID(test_pod["id"])
    messages = message_store.get_all("SLACK")
    assert messages
    assert all(item["channel"] == f"D{actor}" for item in messages)

    # The handoff is what ends signup and puts the personal route in charge of
    # the conversation. It is the worker's job, and this test has no worker.
    async with factory() as uow:
        pending_id = await uow.session.scalar(
            select(PendingChatOnboarding.id).where(
                PendingChatOnboarding.user_id == user_id
            )
        )
    await replay_onboarding(
        pending_id,
        uow_factory=factory,
        job_queue=AsyncMock(spec=SharedStreaqJobQueue),
    )

    # From here the personal route answers instead of `prepare_ingress`, which
    # is where the delivery claim otherwise lives -- so this is the one
    # conversation whose redeliveries have no message-level defence unless the
    # route takes the claim itself. Slack retries a delivery three times.
    redelivered = uuid4().hex
    first = await say("What is on my plate?", ts=redelivered)
    again = await say("What is on my plate?", ts=redelivered)
    assert first.handled and first.context is not None
    assert again.handled and again.context is None


async def test_a_channel_mention_during_signup_is_answered_where_the_room_cannot_read_it(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
):
    """Silence for the whole TTL was the old answer, and it explained nothing.

    A pending signup returns handled-with-no-context for anything that is not a
    DM, which is right as far as routing goes -- nothing may reach an agent
    while nobody has proved who sent it. It was also the *entire* answer, so
    somebody who missed the DM and asked again in the channel got nothing back
    from a bot that was, from where they stood, simply broken.

    PS-SURF-006 is why this is an ephemeral rather than a message: nothing
    about the signup appears in the channel, and nobody else learns that this
    person is halfway through one. It names no address, no code and no account
    status -- only that a DM is waiting.
    """
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-channel-notice",
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
    fake_slack._test_user_email = f"colleague-{uuid4().hex}@gmail.com"
    factory = SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )
    coordinator = ChatOnboardingCoordinator(factory)
    actor = "U" + uuid4().hex[:10]

    async def say(text, *, channel=False):
        payload = _load_slack_dm_fixture(text=text, ts=uuid4().hex)
        payload["event"].update(
            {
                "user": actor,
                "channel": "Ccompany" if channel else f"D{actor}",
                "channel_type": "channel" if channel else "im",
            }
        )
        if channel:
            payload["event"]["type"] = "app_mention"
            payload["event"]["text"] = f"<@U0AGSSTQZLH> {text}"
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="slack", payload=payload)
        )

    assert (await say("Help with my forecast", channel=True)).handled
    message_store.get_all("SLACK_EPHEMERAL").clear()

    assert (await say("hello? are you there", channel=True)).handled

    ephemerals = message_store.get_all("SLACK_EPHEMERAL")
    assert ephemerals, "the second channel mention was answered with nothing at all"
    assert ephemerals[-1]["user"] == actor
    assert ephemerals[-1]["channel"] == "Ccompany"
    assert "direct message" in ephemerals[-1]["text"]
    # And the room itself is told nothing, then or now.
    assert all(
        item["channel"] == f"D{actor}" for item in message_store.get_all("SLACK")
    )
