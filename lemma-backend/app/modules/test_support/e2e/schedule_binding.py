"""Leaving a schedule as provisioning would, for tests that cannot provision.

Only provisioning writes a schedule's routing key (`provider_trigger_id`,
`installation_id`), on the account it subscribed through; an author's config
never carries one. A test whose provider cannot be called for real binds the
row directly instead -- on an account, which is what a routed schedule has.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession


async def bind_schedule_to_account(
    db_session: AsyncSession,
    *,
    schedule_id: str,
    org_id: str,
    user_id: str,
    connector_id: str,
    config: dict[str, object],
) -> None:
    """Leave a schedule as provisioning would: on an account, with the routing
    key the provider gave it."""
    from app.modules.connectors.infrastructure.models.account import Account
    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
    from app.modules.schedule.infrastructure.models.schedule import Schedule

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
    await db_session.execute(
        update(Schedule)
        .where(Schedule.id == schedule_id)
        .values(account_id=account.id, config=config)
    )
    await db_session.commit()
