"""Teams channel signup creates only an installation-owned personal route."""

from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import PersonalDMRoute
from app.modules.agent_surfaces.services.chat_onboarding import (
    ChatOnboardingCoordinator,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _ensure_connector_account,
    _create_agent_surface,
    _load_teams_channel_mention_fixture,
    REAL_TEAMS_TENANT_ID,
)
from app.modules.identity.services.email_challenges import EmailChallengeService
from app.modules.identity.tests.e2e.test_email_challenges_e2e import allow_test_delivery
from app.modules.identity.infrastructure.models.organization_models import (
    Organization,
    OrganizationMember,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("native", [False, True])
async def test_teams_channel_signup_uses_private_cards_and_installation_org(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
    e2e_settings,
    native,
):
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    monkeypatch.setattr(
        surface_settings, "microsoft_bot_app_password", "teams-test-secret"
    )
    email = f"teams-signup-{uuid4().hex}@gmail.com"
    fake_teams._test_user_email = email
    token_cache = RedisJsonCache(
        e2e_settings.redis_url, key_prefix="surface:teams-token", ttl_seconds=3600
    )
    await token_cache.set_raw(
        "botframework.com:https://api.botframework.com/.default", "teams-bot-token"
    )
    await token_cache.close()
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-account-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    await _create_agent_surface(
        authenticated_client,
        test_pod["id"],
        config={"type": "TEAMS", "account_id": str(account.id)},
    )
    organization = await db_session.get(Organization, account.organization_id)
    organization.join_policy = "PUBLIC"
    organization.email_domain = None
    await db_session.commit()
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    codes = []

    async def capture(*, email, code):
        codes.append(code)
        return True

    coordinator = ChatOnboardingCoordinator(
        SessionUnitOfWorkFactory(sessions),
        challenges=EmailChallengeService(
            sessions, send_email=capture, enforce_send_limits=allow_test_delivery
        ),
    )
    actor = str(uuid4())

    async def say(text, *, channel=False):
        payload = _load_teams_channel_mention_fixture(fake_teams)
        payload["from"]["aadObjectId"] = actor
        payload["from"]["id"] = "29:" + actor
        payload["id"] = uuid4().hex
        payload["text"] = text
        if not channel:
            payload["conversation"].update(
                {
                    "id": "personal-onboarding",
                    "conversationType": "personal",
                    "isGroup": False,
                }
            )
            payload["entities"] = []
            payload["channelData"].pop("channel", None)
            payload["channelData"].pop("team", None)
            if native:
                card = message_store.get_all("TEAMS")[-1]["body"]["attachments"][0][
                    "content"
                ]
                payload["value"] = {**card["actions"][0]["data"], "answer": text}
                payload["text"] = ""
        return await coordinator.handle(
            SurfacePlatformWebhookIngress(source="teams", payload=payload)
        )

    assert (await say("<at>Lemma</at> Help me plan", channel=True)).handled
    assert (await say(email)).handled
    assert (await say(codes[0])).handled
    async with sessions() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None and user.mobile_number is None
        membership = await session.scalar(
            select(OrganizationMember).where(OrganizationMember.user_id == user.id)
        )
        assert membership.organization_id == account.organization_id
        route = await session.scalar(
            select(PersonalDMRoute).where(PersonalDMRoute.user_id == user.id)
        )
        assert route is not None and str(route.pod_id) != test_pod["id"]
    messages = message_store.get_all("TEAMS")
    assert messages and all(
        "/personal-onboarding/" in item["path"] for item in messages
    )
    assert any(item["body"].get("attachments") for item in messages)
