"""Re-discovery against a real database.

The property that matters cannot be shown with a mock repository, because it is
a property of the transaction: when re-discovery fails partway, the install must
still have the operations it had before. The previous shape -- delete every row
for the install, then insert the new set -- lost them, since the delete had
already committed its intent by the time an insert collided on the unique index.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.modules.connectors.infrastructure.models.auth_config_operation import (
    AuthConfigOperation,
)
from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (
    AuthConfigOperationRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _op(name: str, tool: str | None = None) -> dict:
    return {
        "name": name,
        "provider_operation_name": tool or name,
        "display_name": tool or name,
        "description": f"{name} description",
        "input_schema": {"type": "object"},
        "execution": {"kind": "mcp", "tool_name": tool or name},
    }


@pytest.fixture
async def install(db_session, connector_test_connector, fixed_test_org):
    """A real auth_config row to hang discovered operations off."""
    from uuid import uuid4

    from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

    auth_config = AuthConfig(
        organization_id=fixed_test_org["id"],
        connector_id=connector_test_connector.id,
        name=f"mcp-{uuid4().hex[:8]}",
        kind="mcp",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        is_default=True,
        config={"server_url": "https://mcp.example.test"},
    )
    db_session.add(auth_config)
    await db_session.commit()
    await db_session.refresh(auth_config)
    return auth_config


async def _names(db_session, auth_config_id) -> set[str]:
    result = await db_session.execute(
        select(AuthConfigOperation.name).where(
            AuthConfigOperation.auth_config_id == auth_config_id
        )
    )
    return set(result.scalars().all())


class TestReplacingTheOperationSet:
    async def test_first_discovery_stores_every_operation(self, db_session, install):
        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("search"), _op("create_issue")],
        )
        await db_session.commit()
        assert await _names(db_session, install.id) == {"search", "create_issue"}

    async def test_rediscovery_adds_updates_and_removes(self, db_session, install):
        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("search"), _op("stale_tool")],
        )
        await db_session.commit()

        # The server has since renamed one tool and added another.
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("search", tool="Search"), _op("brand_new")],
        )
        await db_session.commit()

        assert await _names(db_session, install.id) == {"search", "brand_new"}
        refreshed = await repo.get_by_auth_config_and_name(install.id, "search")
        # The descriptor was updated in place, so it now addresses the tool by
        # the server's current name.
        assert refreshed.execution["tool_name"] == "Search"

    async def test_a_failed_rediscovery_leaves_the_previous_set_intact(
        self, db_session, install
    ):
        """The reason upsert-then-delete is ordered that way."""
        # Held as plain values: the rollback below expires the ORM object, and
        # reloading it mid-assertion would obscure what is being tested.
        install_id, organization_id = install.id, install.organization_id

        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install_id,
            organization_id=organization_id,
            operations=[_op("search"), _op("create_issue")],
        )
        await db_session.commit()

        # Two operations normalizing to the same name: the collision the naming
        # fix now prevents upstream, reproduced here at the storage layer.
        with pytest.raises(Exception):
            await repo.replace_for_auth_config(
                auth_config_id=install_id,
                organization_id=organization_id,
                operations=[_op("duplicate"), _op("duplicate")],
            )
        await db_session.rollback()

        # Still there. Under delete-then-insert this would be empty.
        assert await _names(db_session, install_id) == {"search", "create_issue"}

    async def test_an_empty_discovery_clears_the_set(self, db_session, install):
        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("search")],
        )
        await db_session.commit()
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[],
        )
        await db_session.commit()
        assert await _names(db_session, install.id) == set()


class TestTenantIsolation:
    async def test_operations_are_scoped_to_their_own_install(
        self, db_session, install, connector_test_connector, fixed_test_org
    ):
        from uuid import uuid4

        from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

        other = AuthConfig(
            organization_id=fixed_test_org["id"],
            connector_id=connector_test_connector.id,
            name=f"mcp-other-{uuid4().hex[:8]}",
            kind="mcp",
            config_source="SYSTEM_DEFAULT",
            status="ACTIVE",
            config={"server_url": "https://other.example.test"},
        )
        db_session.add(other)
        await db_session.commit()
        await db_session.refresh(other)

        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("mine")],
        )
        await repo.replace_for_auth_config(
            auth_config_id=other.id,
            organization_id=other.organization_id,
            operations=[_op("theirs")],
        )
        await db_session.commit()

        assert {op.name for op in await repo.list_by_auth_config(install.id)} == {
            "mine"
        }
        assert {op.name for op in await repo.list_by_auth_config(other.id)} == {
            "theirs"
        }

    async def test_deleting_an_install_takes_its_operations_with_it(
        self, db_session, install
    ):
        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("search")],
        )
        await db_session.commit()

        from app.modules.connectors.infrastructure.models.auth_config import AuthConfig

        await db_session.delete(await db_session.get(AuthConfig, install.id))
        await db_session.commit()

        # Enforced by the foreign key, so it holds no matter which code path
        # removed the install.
        remaining = await db_session.execute(
            select(func.count())
            .select_from(AuthConfigOperation)
            .where(AuthConfigOperation.auth_config_id == install.id)
        )
        assert remaining.scalar() == 0
