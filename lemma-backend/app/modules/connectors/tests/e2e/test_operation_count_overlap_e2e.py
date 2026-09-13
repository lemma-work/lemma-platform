"""Counting what an install exposes, without reading it.

The number behind "showing 10 of 340" is a catalog count plus the install's own
discovered set minus the operations both sides name -- and that last term was
computed by reading the whole catalog for the connector, JSONB schemas and all,
to lowercase its names into a Python set.

The count is only worth anything if it agrees with the listing, so that is what
is asserted: the number and the merged list, against the same data. A count
that is cheap and wrong is worse than the read it replaced.
"""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.connectors.infrastructure.repositories.auth_config_operation_repository import (  # noqa: E501
    AuthConfigOperationRepository,
)
from app.modules.connectors.infrastructure.repositories.connector_operation_repository import (  # noqa: E501
    ConnectorOperationRepository,
)
from app.modules.connectors.services.operation_visibility import (
    count_operations_for_install,
    list_operations_for_install,
)
from app.modules.test_support.query_counting import counted_queries

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class _uow:
    """Minimal unit-of-work stand-in: the catalog repository takes a uow."""

    def __init__(self, session):
        self.session = session


def _discovered(name: str) -> dict:
    return {
        "name": name,
        "provider_operation_name": name,
        "display_name": name,
        "description": f"{name} description",
        "input_schema": {"type": "object"},
        "execution": {"kind": "mcp", "tool_name": name},
    }


@pytest.fixture
async def mcp_install(db_session, connector_test_connector, fixed_test_org):
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


async def _seed_catalog(db_session, connector_id: str, names: list[str]) -> None:
    for name in names:
        db_session.add(
            ConnectorOperation(
                # Catalog ids are minted by the import script, not defaulted.
                id=f"{connector_id}:{name}",
                connector_id=connector_id,
                kind="mcp",
                name=name,
                display_name=name,
                description=f"{name} in the catalog",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                execution={"kind": "mcp", "tool_name": name},
            )
        )
    await db_session.commit()


async def test_the_count_agrees_with_the_listing_it_summarizes(
    db_session, mcp_install, connector_test_connector
):
    """The equivalence that makes the count meaningful.

    ``Search`` against ``search`` is the case the shadowing rule exists for and
    the one a case-sensitive join would get wrong: the Python compared
    lowercased names, so the two are one operation, and the total must say 3
    rather than 4.
    """
    await _seed_catalog(db_session, connector_test_connector.id, ["Search", "publish"])
    await AuthConfigOperationRepository(db_session).replace_for_auth_config(
        auth_config_id=mcp_install.id,
        organization_id=mcp_install.organization_id,
        operations=[_discovered("search"), _discovered("create_issue")],
    )
    await db_session.commit()

    catalog_repository = ConnectorOperationRepository(_uow(db_session))
    install_repository = AuthConfigOperationRepository(db_session)

    listed = await list_operations_for_install(
        catalog_repository=catalog_repository,
        install_repository=install_repository,
        connector_id=connector_test_connector.id,
        kind="mcp",
        auth_config_id=mcp_install.id,
    )
    counted = await count_operations_for_install(
        catalog_repository=catalog_repository,
        install_repository=install_repository,
        connector_id=connector_test_connector.id,
        kind="mcp",
        auth_config_id=mcp_install.id,
    )

    assert counted == len(listed), (
        f"counted {counted} but the listing has {len(listed)}: "
        f"{[operation.name for operation in listed]}"
    )
    assert counted == 3


async def test_counting_reads_no_operation_rows(
    db_session, mcp_install, connector_test_connector
):
    """Two counts, and nothing hydrated.

    Asserted on the statements rather than the clock: what made this expensive
    was not how long a row took to read but that rows were read at all, and a
    ``SELECT count`` that has grown a column list is exactly the regression
    worth catching.
    """
    await _seed_catalog(
        db_session,
        connector_test_connector.id,
        [f"catalog_{index}" for index in range(20)],
    )
    await AuthConfigOperationRepository(db_session).replace_for_auth_config(
        auth_config_id=mcp_install.id,
        organization_id=mcp_install.organization_id,
        operations=[_discovered(f"tool_{index}") for index in range(20)],
    )
    await db_session.commit()

    with counted_queries() as statements:
        counted = await count_operations_for_install(
            catalog_repository=ConnectorOperationRepository(_uow(db_session)),
            install_repository=AuthConfigOperationRepository(db_session),
            connector_id=connector_test_connector.id,
            kind="mcp",
            auth_config_id=mcp_install.id,
        )

    assert counted == 40
    reads = [
        statement
        for statement in statements
        if "connector_operations" in statement or "auth_config_operations" in statement
    ]
    assert len(reads) == 2, f"expected two counts, got {len(reads)}: {reads}"
    assert all("count(" in statement.lower() for statement in reads), (
        f"a listing crept back into the count path: {reads}"
    )
    assert not any("input_schema" in statement for statement in reads), (
        f"the count is hydrating rows again: {reads}"
    )
