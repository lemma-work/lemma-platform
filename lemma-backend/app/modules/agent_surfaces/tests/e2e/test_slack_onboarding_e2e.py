"""Company signup stays private and waits for access to the installation org."""

from uuid import UUID, uuid4

import pytest
from slack_sdk.errors import SlackApiError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    PersonalDMRoute,
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

    if initial_dm_refused:
        fake_slack.conversations_open_error = "missing_scope"
        with pytest.raises(SlackApiError):
            await say("Help with my forecast", channel=True)
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
                select(PersonalDMRoute).where(PersonalDMRoute.user_id == user.id)
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
            select(PersonalDMRoute).where(PersonalDMRoute.user_id == user_id)
        )
        assert route is not None and route.installation_surface_id == UUID(
            surface["id"]
        )
        assert route.pod_id != UUID(test_pod["id"])
    messages = message_store.get_all("SLACK")
    assert messages
    assert all(item["channel"] == f"D{actor}" for item in messages)
