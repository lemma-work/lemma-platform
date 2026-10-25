"""Leaving the organization is leaving every pod in it, announced per pod.

Deleting an organization membership cascaded its pod memberships away in the
database, so no ``PodMemberRemoved`` fired and nothing that reacts to a member
leaving a pod ever ran: the person kept their channel seats, nobody was told
the bots they looked after had stopped answering, and their pod accounts stayed.
Now the pod module removes them from each pod through its own removal path
first, and every module that handles a pod departure handles this one.

One observable consequence per module that subscribes to the fact, in each of
two pods:

- channels: the person's channel seats in the pod are gone;
- agent_surfaces: the pod's admin is told the widget they looked after has
  stopped answering;
- connectors: their pod account in the pod is gone.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.infrastructure.models import (
    ChannelMemberModel,
    ChannelMessageModel,
    ChannelModel,
)
from app.modules.connectors.domain.account import AccountEntity
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.pod_connector import PodAccount
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _account(db_session: AsyncSession, *, org_id: str, user_id: str) -> str:
    connector_id = f"crm-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="CRM",
            description="org departure e2e",
            kinds=[{"kind": "http", "auth_scheme": "API_KEY"}],
            is_active=True,
        )
    )
    await db_session.flush()
    install = AuthConfig(
        organization_id=UUID(org_id),
        connector_id=connector_id,
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=connector_id,
    )
    db_session.add(install)
    await db_session.commit()
    created = await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(user_id),
            organization_id=UUID(org_id),
            auth_config_id=install.id,
            connector_id=connector_id,
            is_default=True,
            credentials={"api_key": "alice-key"},
        )
    )
    await db_session.commit()
    return str(created.id)


async def _seats(db_session: AsyncSession, pod_id: str, user_id: str) -> int:
    db_session.expire_all()
    return int(
        await db_session.scalar(
            select(func.count()).where(
                ChannelMemberModel.pod_id == UUID(pod_id),
                ChannelMemberModel.user_id == UUID(user_id),
            )
        )
        or 0
    )


async def _told(db_session: AsyncSession, pod_id: str, user_id: str) -> list[str]:
    """What this member's own channel with the pod has been told."""
    db_session.expire_all()
    rows = await db_session.scalars(
        select(ChannelMessageModel.body)
        .join(ChannelModel, ChannelModel.id == ChannelMessageModel.channel_id)
        .where(
            ChannelModel.pod_id == UUID(pod_id),
            ChannelModel.kind == "DIRECT",
            ChannelModel.member_user_id == UUID(user_id),
        )
    )
    return [body for body in rows if body]


async def _pod_accounts(db_session: AsyncSession, pod_id: str) -> int:
    db_session.expire_all()
    return int(
        await db_session.scalar(
            select(func.count()).where(PodAccount.pod_id == UUID(pod_id))
        )
        or 0
    )


async def test_leaving_the_org_is_leaving_each_pod_for_every_module(
    scenario, db_session, monkeypatch
):
    from app.core.public_web import public_web_settings

    # Web widgets are off by default on a deployment; this one serves them.
    monkeypatch.setattr(public_web_settings, "public_web_enabled", True)
    await scenario.create_org(name_prefix="Departure")
    pods = [
        (await scenario.create_pod(name_prefix="First"))["id"],
        (await scenario.create_pod(name_prefix="Second"))["id"],
    ]
    alice = await scenario.create_user("departing")
    org_member = await invite_org_member(
        scenario.owner_client,
        scenario.async_client,
        org_id=scenario.org_id,
        user=alice,
    )
    account_id = await _account(db_session, org_id=scenario.org_id, user_id=alice["id"])
    as_alice = auth_headers(alice)
    for pod_id in pods:
        await add_pod_member(
            scenario.owner_client,
            pod_id=pod_id,
            organization_member_id=org_member["id"],
            role="POD_EDITOR",
        )
        # channels: her own channel with the pod gives her a seat in it.
        mine = await scenario.async_client.post(
            f"/pods/{pod_id}/channels/me", headers=as_alice
        )
        assert mine.status_code == 201, mine.text
        # agent_surfaces: she looks after a widget's contacts.
        widget = await scenario.owner_client.post(
            f"/pods/{pod_id}/web-widgets",
            json={
                "name": f"help-{uuid4().hex[:6]}",
                "answer": "known",
                "looked_after_by": alice["id"],
            },
        )
        assert widget.status_code in (200, 201), widget.text
        # connectors: her account is in the pod.
        added = await scenario.async_client.post(
            f"/pods/{pod_id}/connector-accounts",
            headers=as_alice,
            json={"account_id": account_id},
        )
        assert added.status_code == 201, added.text
        assert await _seats(db_session, pod_id, alice["id"]) > 0
        assert await _pod_accounts(db_session, pod_id) == 1
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    removed = await scenario.owner_client.delete(
        f"/organizations/{scenario.org_id}/members/{org_member['id']}"
    )
    assert removed.status_code == 204, removed.text
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])

    for pod_id in pods:
        assert await _seats(db_session, pod_id, alice["id"]) == 0, pod_id
        told = await _told(db_session, pod_id, scenario.owner_user["id"])
        assert [line for line in told if "has left the space" in line], pod_id
        assert await _pod_accounts(db_session, pod_id) == 0, pod_id
