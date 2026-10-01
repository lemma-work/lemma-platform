"""The importer switching off installs Composio can no longer serve.

A Lemma-default install of a Composio toolkit was made while Composio held
credentials for it. When a later catalog import finds Composio no longer does,
connecting through that install asks for managed credentials that do not exist,
and Composio's 404 reached the person as a 502. The sweep disables those
installs -- never the org's own, never a toolkit Composio still manages -- and
flags the accounts on them for reconnection rather than deleting anything.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountStatus
from app.modules.connectors.domain.auth_config import (
    AuthConfigSource,
    AuthConfigStatus,
)
from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_repository import (
    ConnectorRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_IMPORTER_PATH = (
    Path(__file__).resolve().parents[5] / "scripts" / "import_connector_catalog.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "import_connector_catalog", _IMPORTER_PATH
)
assert _SPEC and _SPEC.loader
importer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(importer)


async def _toolkit(db_session, *, managed: bool) -> str:
    connector_id = f"toolkit-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="Toolkit",
            description="A Composio toolkit.",
            kinds=[
                {
                    "kind": "composio",
                    "auth_scheme": "OAUTH2",
                    "toolkit_slug": connector_id,
                    "system_default_available": managed,
                    "supports_org_custom_oauth": not managed,
                }
            ],
            is_active=True,
        )
    )
    await db_session.flush()
    return connector_id


async def _install(
    db_session, *, connector_id: str, org_id: UUID, source: AuthConfigSource
) -> AuthConfig:
    install = AuthConfig(
        organization_id=org_id,
        connector_id=connector_id,
        kind="composio",
        config_source=source.value,
        is_default=source is AuthConfigSource.SYSTEM_DEFAULT,
        name=f"{connector_id}-{source.value.lower()}-{uuid4().hex[:6]}",
    )
    db_session.add(install)
    await db_session.flush()
    return install


async def _account(db_session, *, install: AuthConfig, user_id: UUID) -> Account:
    account = Account(
        user_id=user_id,
        organization_id=install.organization_id,
        auth_config_id=install.id,
        connector_id=install.connector_id,
        status=AccountStatus.CONNECTED.value,
    )
    db_session.add(account)
    await db_session.flush()
    return account


async def _sweep(db_session) -> int:
    uow = SqlAlchemyUnitOfWork(db_session)
    return await importer._disable_unmanaged_composio_defaults(
        ConnectorRepository(uow),
        AccountRepository(uow),
        db_session,
    )


async def test_a_lemma_default_install_of_an_unmanaged_toolkit_is_switched_off(
    db_session, fixed_test_org, fixed_test_user
):
    org_id = UUID(str(fixed_test_org["id"]))
    user_id = UUID(str(fixed_test_user["id"]))
    unmanaged = await _toolkit(db_session, managed=False)
    managed = await _toolkit(db_session, managed=True)
    stale = await _install(
        db_session,
        connector_id=unmanaged,
        org_id=org_id,
        source=AuthConfigSource.SYSTEM_DEFAULT,
    )
    theirs = await _install(
        db_session,
        connector_id=unmanaged,
        org_id=org_id,
        source=AuthConfigSource.ORG_CUSTOM,
    )
    still_managed = await _install(
        db_session,
        connector_id=managed,
        org_id=org_id,
        source=AuthConfigSource.SYSTEM_DEFAULT,
    )
    stranded = await _account(db_session, install=stale, user_id=user_id)
    on_their_app = await _account(db_session, install=theirs, user_id=user_id)

    assert await _sweep(db_session) >= 1

    rows = {
        row.id: row
        for row in (
            await db_session.scalars(
                select(AuthConfig).where(
                    AuthConfig.id.in_([stale.id, theirs.id, still_managed.id])
                )
            )
        )
    }
    for row in rows.values():
        await db_session.refresh(row)
    assert rows[stale.id].status == AuthConfigStatus.DISABLED.value
    assert rows[stale.id].is_default is False
    # The org's own app, and a toolkit Composio still manages, are untouched.
    assert rows[theirs.id].status == AuthConfigStatus.ACTIVE.value
    assert rows[still_managed.id].status == AuthConfigStatus.ACTIVE.value
    assert rows[still_managed.id].is_default is True

    await db_session.refresh(stranded)
    await db_session.refresh(on_their_app)
    # Flagged, not deleted: the account keeps its id and shows a reconnect.
    assert stranded.status == AccountStatus.REAUTH_REQUIRED.value
    assert on_their_app.status == AccountStatus.CONNECTED.value


async def test_the_sweep_is_a_no_op_once_applied(
    db_session, fixed_test_org, fixed_test_user
):
    org_id = UUID(str(fixed_test_org["id"]))
    unmanaged = await _toolkit(db_session, managed=False)
    stale = await _install(
        db_session,
        connector_id=unmanaged,
        org_id=org_id,
        source=AuthConfigSource.SYSTEM_DEFAULT,
    )

    await _sweep(db_session)
    await _sweep(db_session)

    await db_session.refresh(stale)
    assert stale.status == AuthConfigStatus.DISABLED.value
    remaining = await db_session.scalar(
        select(AuthConfig.id).where(
            AuthConfig.connector_id == unmanaged,
            AuthConfig.status != AuthConfigStatus.DISABLED.value,
        )
    )
    assert remaining is None
