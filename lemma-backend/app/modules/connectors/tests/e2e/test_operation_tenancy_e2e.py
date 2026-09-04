"""One tenant's discovered operations must never reach another.

`GET /connectors/{id}` is a global catalog route: authenticated, but with no
organization anywhere in the path. It returned every operation row for the
connector, and discovered operations used to live in that same table
distinguished only by a nullable `auth_config_id` that the catalog queries
forgot to filter on. For a shared connector id like `mcp`, that meant every
organization's MCP tool names, descriptions and input schemas -- a description of
their internal systems -- were readable by any logged-in user.

The fix is the table split, so these are regression tests for a bug class rather
than for one query: the catalog table has no `auth_config_id` column at all, and
therefore cannot hold a tenant row for a forgotten predicate to return.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect, select

from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.auth_config_operation import (
    AuthConfigOperation,
)
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (
    AuthConfigOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (
    ConnectorOperationRepository,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class _uow:
    """Minimal unit-of-work stand-in: these repositories take a uow, not a session."""

    def __init__(self, session):
        self.session = session


def _op(name: str) -> dict:
    return {
        "name": name,
        "provider_operation_name": name,
        "display_name": name,
        "description": f"internal tool: {name}",
        "input_schema": {
            "type": "object",
            "properties": {"secret_arg": {"type": "string"}},
        },
        "execution": {"kind": "mcp", "tool_name": name},
    }


async def _install(db_session, connector_id: str, organization_id, label: str):
    auth_config = AuthConfig(
        organization_id=organization_id,
        connector_id=connector_id,
        name=f"{label}-{uuid4().hex[:8]}",
        kind="mcp",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        config={"server_url": f"https://{label}.internal.test"},
    )
    db_session.add(auth_config)
    await db_session.commit()
    await db_session.refresh(auth_config)
    return auth_config


def test_the_catalog_table_cannot_hold_a_tenant_row():
    """The structural guarantee, asserted directly.

    While `connector_operations` had a nullable `auth_config_id`, every catalog
    query needed to remember `WHERE auth_config_id IS NULL`, and four of them did
    not. There is no column to forget now.
    """
    columns = {c.name for c in inspect(ConnectorOperation).columns}
    assert "auth_config_id" not in columns
    assert "organization_id" not in columns

    # And the tenant table is anchored to both its install and its owner, so a
    # tenant query always has an organization predicate available without a join.
    tenant_columns = {c.name for c in inspect(AuthConfigOperation).columns}
    assert {"auth_config_id", "organization_id"} <= tenant_columns


class TestCatalogReadsSeeNoTenantData:
    async def test_discovered_operations_are_invisible_to_catalog_queries(
        self, db_session, connector_test_connector, fixed_test_org
    ):
        install = await _install(
            db_session, connector_test_connector.id, fixed_test_org["id"], "acme"
        )
        await AuthConfigOperationRepository(db_session).replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("read_payroll"), _op("list_customers")],
        )
        await db_session.commit()

        # This is the query behind GET /connectors/{id}, which has no org scope.
        catalog = await ConnectorOperationRepository(
            _uow(db_session)
        ).list_by_connector(connector_test_connector.id)
        assert [op.name for op in catalog] == []

    async def test_a_named_lookup_cannot_reach_a_discovered_operation(
        self, db_session, connector_test_connector, fixed_test_org
    ):
        install = await _install(
            db_session, connector_test_connector.id, fixed_test_org["id"], "acme"
        )
        await AuthConfigOperationRepository(db_session).replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("read_payroll")],
        )
        await db_session.commit()

        repo = ConnectorOperationRepository(_uow(db_session))
        assert (
            await repo.get_by_connector_and_name(
                connector_test_connector.id, "read_payroll"
            )
            is None
        )
        assert (
            await repo.get_by_connector_kind_and_name(
                connector_test_connector.id, "mcp", "read_payroll"
            )
            is None
        )

    async def test_search_cannot_surface_another_tenants_tool_descriptions(
        self, db_session, connector_test_connector, fixed_test_org
    ):
        # The descriptions are the interesting part: they describe a customer's
        # own systems in prose.
        install = await _install(
            db_session, connector_test_connector.id, fixed_test_org["id"], "acme"
        )
        await AuthConfigOperationRepository(db_session).replace_for_auth_config(
            auth_config_id=install.id,
            organization_id=install.organization_id,
            operations=[_op("read_payroll")],
        )
        await db_session.commit()

        found = await ConnectorOperationRepository(_uow(db_session)).list_by_connector(
            connector_test_connector.id, search_query="payroll"
        )
        assert found == []


class TestTwoOrganizationsOnOneConnector:
    async def test_neither_org_can_see_the_others_operations(
        self, db_session, connector_test_connector, fixed_test_org
    ):
        """Two installs of the same shared connector id, as `mcp` really is."""
        from app.modules.identity.infrastructure.models.organization_models import (
            Organization,
        )

        other_org = Organization(
            id=uuid4(),
            name="Other Co",
            slug=f"other-{uuid4().hex[:8]}",
            join_policy="INVITE_ONLY",
        )
        db_session.add(other_org)
        await db_session.commit()

        mine = await _install(
            db_session, connector_test_connector.id, fixed_test_org["id"], "acme"
        )
        theirs = await _install(
            db_session, connector_test_connector.id, other_org.id, "other"
        )

        repo = AuthConfigOperationRepository(db_session)
        await repo.replace_for_auth_config(
            auth_config_id=mine.id,
            organization_id=mine.organization_id,
            operations=[_op("acme_only_tool")],
        )
        await repo.replace_for_auth_config(
            auth_config_id=theirs.id,
            organization_id=theirs.organization_id,
            operations=[_op("other_only_tool")],
        )
        await db_session.commit()

        assert {op.name for op in await repo.list_by_auth_config(mine.id)} == {
            "acme_only_tool"
        }
        assert {op.name for op in await repo.list_by_auth_config(theirs.id)} == {
            "other_only_tool"
        }

        # And every stored row carries its owner, so an org-scoped query needs no
        # join to be safe.
        rows = await db_session.execute(
            select(AuthConfigOperation.organization_id, AuthConfigOperation.name)
        )
        by_name = {name: org for org, name in rows.all()}
        assert by_name["acme_only_tool"] == mine.organization_id
        assert by_name["other_only_tool"] == theirs.organization_id
