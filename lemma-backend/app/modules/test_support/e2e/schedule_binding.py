"""Leaving a schedule as provisioning would, for tests that cannot provision.

Only provisioning writes a schedule's routing key (`provider_trigger_id`,
`installation_id`), through the pod connector it listens through; an author's
config never carries one. A test whose provider cannot be called for real binds
the row directly instead -- to a pod connector that resolves, for the schedule's
owner, to an account of theirs, which is what a routed schedule has.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def bind_schedule_to_connector(
    db_session: AsyncSession,
    *,
    schedule_id: str,
    org_id: str,
    user_id: str,
    connector_id: str,
    config: dict[str, object],
) -> UUID:
    """Leave a schedule as provisioning would: listening through a per-member
    pod connector whose owner's account is in the pod, with the routing key
    the provider gave it. The account's id, for a test that asserts on it."""
    from app.modules.connectors.infrastructure.models.account import Account
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )
    from app.modules.schedule.infrastructure.models.schedule import Schedule

    pod_id = (
        await db_session.execute(
            select(Schedule.pod_id).where(Schedule.id == schedule_id)
        )
    ).scalar_one()
    auth_config = AuthConfig(
        organization_id=org_id,
        connector_id=connector_id,
        kind="http",
        name=f"{connector_id}-{uuid4().hex[:8]}",
    )
    db_session.add(auth_config)
    await db_session.flush()
    account = Account(
        user_id=user_id,
        organization_id=org_id,
        connector_id=connector_id,
        auth_config_id=auth_config.id,
        credentials={"access_token": "x"},
    )
    db_session.add(account)
    await db_session.flush()
    db_session.add(
        PodAccount(
            pod_id=pod_id,
            account_id=account.id,
            auth_config_id=auth_config.id,
            owner_user_id=user_id,
            visibility="PERSONAL",
            is_member_default=True,
            source="MANUAL",
        )
    )
    slug = f"{connector_id}-{uuid4().hex[:6]}"
    db_session.add(
        PodConnector(
            pod_id=pod_id,
            slug=slug,
            auth_config_id=auth_config.id,
            mode="PER_MEMBER",
            created_by_user_id=user_id,
        )
    )
    await db_session.flush()
    await db_session.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id)
        .values(pod_connector_slug=slug, config=config)
    )
    await db_session.commit()
    return account.id
