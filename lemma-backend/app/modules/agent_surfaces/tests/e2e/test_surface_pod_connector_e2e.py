"""A surface runs as a SHARED pod connector, resolved at every use.

A surface used to store an ``account_id``: the binder's own account, checked
once when it was bound and never again, so it kept running on a credential its
owner had stopped offering. Now it names a pod connector, and the account
behind it is re-read through the connector each time the surface needs it --
the owner withdrawing it stops the surface at once, with the connector's own
typed refusal.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.credentials import WhatsAppCredentials
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_agent,
    _ensure_connector_account,
    share_account_as_connector,
)
from app.modules.connectors.domain.pod_connector_errors import (
    ConnectorNeedsAccountError,
)
from app.modules.test_support.e2e import fixtures as e2e_fixtures

pytestmark = pytest.mark.e2e

module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user
fixed_test_org = e2e_fixtures.module_test_org


async def _whatsapp_account(db_session: AsyncSession, user_id: str):
    return await _ensure_connector_account(
        db_session,
        user_id=user_id,
        connector_id="whatsapp",
        credentials={
            "access_token": "the-orgs-own-token",
            "phone_number_id": "PN-OWN",
            "app_secret": "the-orgs-own-app-secret",
            "verify_token": "own-verify",
        },
    )


async def _surface(client: AsyncClient, pod_id: str, agent: dict, slug: str):
    return await client.post(
        f"/pods/{pod_id}/surfaces",
        json={
            "platform": "WHATSAPP",
            "credential_mode": "CUSTOM",
            "default_agent_name": agent["name"],
            "pod_connector": slug,
        },
    )


async def _credentials(surface_id: str):
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        surface = await SurfaceRepository(uow).get(UUID(surface_id))
        assert surface is not None
        return await SurfaceCredentialResolver(uow=uow).for_surface(surface)


async def test_a_surface_stops_the_moment_its_owner_withdraws_the_account(
    authenticated_client, db_session, test_pod, fixed_test_user
):
    pod_id = test_pod["id"]
    account = await _whatsapp_account(db_session, fixed_test_user["id"])
    slug = await share_account_as_connector(
        authenticated_client, pod_id, account.id, slug="support-whatsapp"
    )
    agent = await _create_agent(authenticated_client, pod_id)

    created = await _surface(authenticated_client, pod_id, agent, slug)
    assert created.status_code == 200, created.text
    surface = created.json()
    assert surface["pod_connector"] == slug
    assert surface["connection"]["account_id"] == str(account.id)
    assert surface["connection"]["status"] == "CONNECTED"

    credentials = WhatsAppCredentials.of(await _credentials(surface["id"]))
    assert credentials.phone_number_id == "PN-OWN"

    pod_account_id = (
        await authenticated_client.get(f"/pods/{pod_id}/connector-accounts")
    ).json()["items"][0]["id"]
    withdrawn = await authenticated_client.patch(
        f"/pods/{pod_id}/connector-accounts/{pod_account_id}",
        json={"visibility": "PERSONAL"},
    )
    assert withdrawn.status_code == 200, withdrawn.text

    with pytest.raises(ConnectorNeedsAccountError):
        await _credentials(surface["id"])
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/surfaces/{surface['name']}"
    )
    assert listed.json()["connection"]["status"] == "MISSING"


async def test_a_per_member_connector_cannot_back_a_surface(
    authenticated_client, db_session, test_pod, fixed_test_user
):
    """A surface answers with nobody at the keyboard, so there is no member
    whose own account a per-member connector could mean."""
    pod_id = test_pod["id"]
    account = await _whatsapp_account(db_session, fixed_test_user["id"])
    declared = await authenticated_client.post(
        f"/pods/{pod_id}/connectors",
        json={
            "slug": "whatsapp",
            "auth_config_id": str(account.auth_config_id),
            "mode": "PER_MEMBER",
        },
    )
    assert declared.status_code == 201, declared.text
    agent = await _create_agent(authenticated_client, pod_id)

    refused = await _surface(authenticated_client, pod_id, agent, "whatsapp")

    assert refused.status_code == 422, refused.text
    assert "shared connector" in refused.json()["message"]


async def test_another_platforms_connector_cannot_back_a_surface(
    authenticated_client, db_session, test_pod, fixed_test_user
):
    pod_id = test_pod["id"]
    telegram = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={"bot_token": "123:abc"},
    )
    slug = await share_account_as_connector(authenticated_client, pod_id, telegram.id)
    agent = await _create_agent(authenticated_client, pod_id)

    refused = await _surface(authenticated_client, pod_id, agent, slug)

    assert refused.status_code == 422, refused.text
    assert "whatsapp connector" in refused.json()["message"]


async def test_a_connector_the_pod_does_not_have_is_not_found(
    authenticated_client, test_pod
):
    pod_id = test_pod["id"]
    agent = await _create_agent(authenticated_client, pod_id)

    refused = await _surface(authenticated_client, pod_id, agent, "no-such-bot")

    assert refused.status_code == 404, refused.text
    assert refused.json()["code"] == "POD_CONNECTOR_NOT_FOUND"
