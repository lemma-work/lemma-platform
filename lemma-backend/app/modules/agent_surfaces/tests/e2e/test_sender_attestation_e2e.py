"""Who a Slack message is from, believed only on what can vouch for it.

Against the real ingress, tables and the fake Slack Web API:

* a profile phone -- which its owner types -- never makes a sender the member
  whose verified number it copies;
* a profile email -- which a workspace admin can set -- never makes a sender
  the member it names; with no proof in the workspace they are somebody to
  verify, not that member;
* on a teammate app the org brought -- made by hand, its signing secret pasted
  in, so a person holds it and can sign any sender id -- a proof made through
  Lemma's own app does not name the sender; a binding made through that same
  app does. A teammate app Lemma made is not one of those, and the Allow that
  binds it records which it is -- on the app's install, which every surface on
  the app reads through its pod connector.
"""

from __future__ import annotations

from app.modules.agent_surfaces.tests.e2e.helpers import share_account_as_connector

from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _bound_through_installation,
    _create_agent,
    _ensure_connector_account,
    _set_user_mobile_number,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    slack as slack_payloads,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    clean_surface_work,  # noqa: F401  (fixture)
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import drain
from app.modules.agent_surfaces.tests.e2e.test_slack_manager_e2e import (
    _allowed,
    _awaiting_allow,
    _bound,
    _id,
    _registry,
    lemmas_app,  # noqa: F401  (fixture)
)
from app.modules.agent_surfaces.tests.e2e.test_slack_workspace_contacts_e2e import (
    _Bot,
    _colleague,
    _held_bot,
    _prepare,
    _prove,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.drives_work,
    pytest.mark.usefixtures("clean_surface_work", "lemmas_app"),
]

_ADMINS_NUMBER = "+15550142201"


def _ran_as(context: Any, user: dict) -> bool:
    """Did ingress hand this message to a run as ``user`` themselves?

    A contact's run is looked after by whoever connected the bot -- which may be
    the same person -- so a contact audience is never "as them".
    """
    return (
        isinstance(context, SurfaceChatContext)
        and not context.audience.is_contact
        and str(context.user_id) == user["id"]
    )


async def _lemmas_own_app_as_the_pods_bot(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user_id: str,
    fake_slack,
) -> None:
    """A Slack bot on Lemma's own app, in a workspace nobody holds: no proof of
    anybody exists there yet."""
    account = await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="slack",
        credentials={
            "access_token": "xoxb-sender-attestation",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": slack_payloads.BOT_USER_ID,
                "team_id": slack_payloads.TEAM_ID,
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent = await _create_agent(client, pod_id)
    response = await client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "SLACK",
            "pod_connector": await share_account_as_connector(
                client, pod_id, account.id
            ),
            "default_agent_name": agent["name"],
        },
    )
    assert response.status_code == 200, response.text


def _dm_from(sender: str) -> SurfacePlatformWebhookIngress:
    payload = slack_payloads.dm(
        text="what changed in the launch plan?",
        ts=f"1700000000.{uuid4().int % 1_000_000:06d}",
    )
    payload["event"]["user"] = sender
    return SurfacePlatformWebhookIngress(source="slack", payload=payload, headers={})


# -- a phone the sender typed ------------------------------------------------


async def test_a_slack_profile_phone_copying_an_admins_verified_number_is_not_the_admin(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    await _lemmas_own_app_as_the_pods_bot(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )
    await _set_user_mobile_number(
        db_session, user_id=fixed_test_user["id"], mobile_number=_ADMINS_NUMBER
    )
    impostor = _colleague(fake_slack, phone=_ADMINS_NUMBER)

    context = await _prepare(_dm_from(impostor))

    assert not _ran_as(context, fixed_test_user), context


# -- an email an admin can set -----------------------------------------------


async def test_a_slack_profile_email_with_no_proof_is_not_the_member_it_names(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    """The fake's default profile carries the pod member's own email."""
    await _lemmas_own_app_as_the_pods_bot(
        authenticated_client,
        db_session,
        pod_id=test_pod["id"],
        user_id=fixed_test_user["id"],
        fake_slack=fake_slack,
    )

    context = await _prepare(_dm_from(_id("U")))

    assert not _ran_as(context, fixed_test_user), context


# -- a sender id the org's own secret signed ---------------------------------


@pytest.mark.parametrize("cache_cleared", [False, True])
async def test_a_proof_through_lemmas_app_does_not_name_a_sender_on_an_app_the_org_brought(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    cache_cleared,
):
    """Whoever holds the teammate app's signing secret can sign this very body.

    ``_held_bot``'s teammate is an org install nothing marks as Lemma's: one
    the org brought.

    The proof was written through to the workspace's identity cache, so both
    are asked: with the cache, and with only the proof left.
    """
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    proven = _colleague(fake_slack)
    await _prove(bot.workspace.team, proven, fixed_test_user["id"])
    if cache_cleared:
        from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
            ExternalSurfaceUserRepository,
        )

        async with async_session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await ExternalSurfaceUserRepository(uow).clear_resolved_user(
                UUID(fixed_test_user["id"])
            )
            await uow.commit()

    context = await _prepare(bot.says(proven))

    assert not _ran_as(context, fixed_test_user), context


async def test_a_binding_made_through_an_app_the_org_brought_names_the_sender(
    authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
):
    bot = await _held_bot(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    me = _colleague(fake_slack)
    surface = await db_session.get(AgentSurface, UUID(bot.surface["id"]))
    assert surface is not None and surface.pod_connector_slug is not None
    # The binding is to the app itself: the account its connector runs as.
    assert bot.surface["connection"]["account_id"]
    await _bound_through_installation(
        db_session,
        platform="SLACK",
        tenant_id=bot.workspace.team,
        installation_id=UUID(bot.surface["connection"]["account_id"]),
        sender=me,
        user_id=fixed_test_user["id"],
    )

    context = await _prepare(bot.says(me))

    assert _ran_as(context, fixed_test_user), context


# -- which teammate apps the org brought --------------------------------------


@pytest.mark.parametrize(
    ("lemma_made_it", "named"), [(True, True), (False, False)], ids=["lemma", "org"]
)
async def test_the_allow_records_who_made_the_app_and_a_proof_follows_it(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
    lemma_made_it,
    named,
):
    """Lemma knows a teammate app's id only when it made the app; one made by
    hand arrives as three pasted values. Recorded on the app's install when
    the Allow binds it, because the pending row that knew is deleted then --
    and not on the surface, which a re-pointed connector would leave saying
    it of another app."""
    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    bot, account, pending = await _awaiting_allow(
        db_session,
        workspace=workspace,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_slack=fake_slack,
    )
    install_id = account.auth_config_id
    if not lemma_made_it:
        pending.app_id = None
        await db_session.commit()
    await _allowed(
        db_session, pending=pending, account=account, purpose="slack_teammate_app"
    )
    await drain(_registry())
    db_session.expire_all()
    # The Allow shared the new account into the pod as a SHARED connector and
    # bound the teammate's surface to it.
    surface = await db_session.scalar(
        select(AgentSurface).where(
            AgentSurface.pod_id == UUID(test_pod["id"]),
            AgentSurface.pod_connector_slug.is_not(None),
            AgentSurface.surface_type == "SLACK",
        )
    )
    assert surface is not None
    assert "app_made_by_lemma" not in (surface.config.get("slack") or {})
    assert (await _install_metadata(db_session, install_id)).get(
        "app_made_by_lemma", False
    ) is lemma_made_it
    proven = _colleague(fake_slack)
    await _prove(workspace.team, proven, fixed_test_user["id"])

    context = await _prepare(_Bot(workspace, {"id": str(surface.id)}, bot).says(proven))

    assert _ran_as(context, fixed_test_user) is named, context


async def test_a_lemma_made_app_whose_credentials_are_replaced_is_trusted_no_more(
    authenticated_client,
    db_session,
    test_pod,
    fixed_test_user,
    fake_slack,
):
    """Whoever replaces a Lemma-made app's credentials chose them, signing
    secret included, so the install stops saying Lemma made it -- and every
    surface on it reads its deliveries as that person's word."""
    from app.modules.agent_surfaces.services.customer_held_secrets import (
        signed_with_a_customer_secret,
    )

    workspace = await _bound(
        authenticated_client, db_session, test_pod, fixed_test_user, fake_slack
    )
    _, account, pending = await _awaiting_allow(
        db_session,
        workspace=workspace,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_slack=fake_slack,
    )
    install_id = account.auth_config_id
    await _allowed(
        db_session, pending=pending, account=account, purpose="slack_teammate_app"
    )
    await drain(_registry())

    async def customer_held() -> bool:
        async with async_session_maker() as session:
            row = await session.scalar(
                select(AgentSurface).where(
                    AgentSurface.pod_id == UUID(test_pod["id"]),
                    AgentSurface.pod_connector_slug.is_not(None),
                    AgentSurface.surface_type == "SLACK",
                )
            )
            return await signed_with_a_customer_secret(
                SqlAlchemyUnitOfWork(session), row.to_entity()
            )

    assert await customer_held() is False
    name = await db_session.scalar(
        select(AuthConfig.name).where(AuthConfig.id == install_id)
    )
    replaced = await authenticated_client.patch(
        f"/organizations/{test_pod['organization_id']}/connectors/auth-configs/{name}",
        json={
            "config": {
                "client_id": "1.2",
                "client_secret": "chosen",
                "signing_secret": "one-a-person-chose",
            }
        },
    )
    assert replaced.status_code == 200, replaced.text
    metadata = await _install_metadata(db_session, install_id)
    assert "app_made_by_lemma" not in metadata
    assert await customer_held() is True


async def _install_metadata(db_session: AsyncSession, auth_config_id) -> dict:
    # A column read, so it is the row as it is now, with nothing expired.
    return (
        await db_session.scalar(
            select(AuthConfig.metadata_).where(AuthConfig.id == auth_config_id)
        )
        or {}
    )
