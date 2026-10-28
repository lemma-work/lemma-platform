"""What another pod may reach here, decided against real grant rows.

Billing lets Support ask it, sharing ``invoices``. A run in Billing answering
Support is authorized as ``POD:Support`` (``core/authorization/pod_principal``),
and two things decide what it reaches: the authorizer, per call, and the SQL
projection that lists rows (``sql_actions._pod_allowed_actions_expr``). Both are
held here against Postgres -- the table rows they project, the decisions they
return -- including grant rows no route would write: a write on a shared table,
a grant on somebody's own table, a grant made in a third pod.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import select, text

from app.core.authorization.context import ResourceRef
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.authorization.pod_principal import POD_PRINCIPAL_TYPE, build_pod_context
from app.core.authorization.sql_actions import allowed_actions_expr
from app.core.authorization.context import ResourceType
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.tests.e2e.pod_link_helpers import (
    add_rows,
    connect,
    table_share,
)
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.pod.models import (
    PodGetRecordsRequest,
    PodTablesRequest,
    QueryRequest,
)
from app.modules.agent.infrastructure.tools.pod.tools import (
    pod_get_records,
    pod_query,
    pod_tables,
)
from app.modules.datastore.infrastructure.models import DatastoreTable
from app.modules.test_support.e2e.function_helpers import create_table

pytestmark = pytest.mark.e2e

_READS = {Permissions.DATASTORE_TABLE_READ, Permissions.DATASTORE_RECORD_READ}


async def _table(client, pod_id: str, name: str, **kwargs) -> UUID:
    made = await create_table(client, pod_id, name, enable_rls=False, **kwargs)
    return UUID(made["id"])


async def _grant(*, pod_id: str, table_id: UUID, to_pod: str, permission: str) -> None:
    """A grant row to ``POD:to_pod``, written as no route would write it."""
    async with async_session_maker() as session:
        session.add(
            ResourcePermissionGrantModel(
                pod_id=UUID(pod_id),
                resource_type=ResourceType.DATASTORE_TABLE.value,
                resource_id=table_id,
                grantee_type=POD_PRINCIPAL_TYPE,
                grantee_id=UUID(to_pod),
                permission_id=permission,
            )
        )
        await session.commit()


async def _projected(*, answering: str, asking: str, pods: list[str]) -> dict:
    """``{table name: allowed actions}`` for every table in ``pods``, as the
    answering pod's listing would project them for the asking pod."""
    async with async_session_maker() as session:
        ctx = build_pod_context(
            session=session,
            pod_id=UUID(answering),
            organization_id=None,
            asking_pod_id=UUID(asking),
        )
        actions = allowed_actions_expr(
            ctx=ctx,
            resource_type=ResourceType.DATASTORE_TABLE,
            resource_id_col=DatastoreTable.id,
            pod_id_col=DatastoreTable.pod_id,
            owner_user_id_col=DatastoreTable.user_id,
            visibility_col=DatastoreTable.visibility,
        )
        rows = await session.execute(
            select(DatastoreTable.table_name, actions).where(
                DatastoreTable.pod_id.in_([UUID(pod) for pod in pods])
            )
        )
        return {name: set(allowed or ()) for name, allowed in rows}


async def _decision(*, answering: str, asking: str, permission: str, ref: ResourceRef):
    async with async_session_maker() as session:
        ctx = build_pod_context(
            session=session,
            pod_id=UUID(answering),
            organization_id=None,
            asking_pod_id=UUID(asking),
        )
        return await ctx.authorizer.authorize(ctx, permission, ref)


def _link_run(*, user_id: str, org_id: str, answering: str, asking: str):
    """The tool context of a run in ``answering`` answering ``asking``."""
    return ToolContext(
        user_id=UUID(user_id),
        org_id=UUID(org_id),
        pod_id=UUID(answering),
        conversation_id=uuid4(),
        audience=Audience.pod(UUID(asking)),
    )


@pytest.mark.asyncio
async def test_another_pod_reaches_its_grants_and_public_rows_and_nothing_else(
    authenticated_client, fixed_test_org, fixed_test_user
):
    client = authenticated_client
    support = await _create_test_pod(client, fixed_test_org)
    billing = await _create_test_pod(client, fixed_test_org)
    third = await _create_test_pod(client, fixed_test_org)

    invoices = await _table(client, billing, "invoices")
    payroll = await _table(client, billing, "payroll")
    await _table(client, billing, "notices", visibility="PUBLIC")
    mine = await _table(client, billing, "mine", visibility="PERSONAL")
    elsewhere = await _table(client, third, "elsewhere")
    await add_rows(client, billing, "invoices", "inv-1", "inv-2")
    await add_rows(client, billing, "payroll", "salary")
    await add_rows(client, billing, "notices", "closed friday")

    shared = await connect(
        client, answering=billing, asking=support, shares=[table_share("invoices")]
    )
    assert shared.status_code == status.HTTP_204_NO_CONTENT, shared.text
    # The third pod shares its own table with Support, through its own link.
    other = await connect(
        client, answering=third, asking=support, shares=[table_share("elsewhere")]
    )
    assert other.status_code == status.HTTP_204_NO_CONTENT, other.text
    # Rows no route writes: a write on the shared table, and a read on a table
    # that is somebody's own.
    await _grant(
        pod_id=billing,
        table_id=invoices,
        to_pod=support,
        permission=Permissions.DATASTORE_RECORD_WRITE,
    )
    await _grant(
        pod_id=billing,
        table_id=mine,
        to_pod=support,
        permission=Permissions.DATASTORE_TABLE_READ,
    )

    # The listing projection, row by row, across Billing and the third pod.
    seen = await _projected(answering=billing, asking=support, pods=[billing, third])
    assert seen["invoices"] == _READS
    assert _READS <= seen["notices"]
    assert seen["notices"] <= {a for a in seen["notices"] if a.endswith(".read")}
    assert seen["payroll"] == set()
    assert seen["mine"] == set()
    assert seen["elsewhere"] == set()
    # The third pod's grant is real: answering *there*, Support reaches it.
    there = await _projected(answering=third, asking=support, pods=[third])
    assert there["elsewhere"] == _READS

    # The authorizer, per call, agrees.
    async def decide(permission: str, pod: str, table_id: UUID):
        return await _decision(
            answering=billing,
            asking=support,
            permission=permission,
            ref=ResourceRef.table(UUID(pod), table_id),
        )

    read = await decide(Permissions.DATASTORE_RECORD_READ, billing, invoices)
    assert read.allowed, read
    write = await decide(Permissions.DATASTORE_RECORD_WRITE, billing, invoices)
    assert (write.allowed, write.reason_code) == (False, "POD_READ_ONLY")
    kept = await decide(Permissions.DATASTORE_RECORD_READ, billing, payroll)
    assert (kept.allowed, kept.reason_code) == (False, "MISSING_POD_GRANT")
    personal = await decide(Permissions.DATASTORE_TABLE_READ, billing, mine)
    assert (personal.allowed, personal.reason_code) == (
        False,
        "PERSONAL_RESOURCE_DENIED",
    )
    through_b = await decide(Permissions.DATASTORE_RECORD_READ, third, elsewhere)
    assert (through_b.allowed, through_b.reason_code) == (False, "POD_SCOPE_MISMATCH")

    # And what a link run's tools actually hand back.
    ctx = _link_run(
        user_id=fixed_test_user["id"],
        org_id=fixed_test_org["id"],
        answering=billing,
        asking=support,
    )
    listed = await pod_tables(ctx, PodTablesRequest())
    assert listed["success"] is True, listed
    assert {table["name"] for table in listed["tables"]} == {"invoices", "notices"}
    rows = await pod_get_records(ctx, PodGetRecordsRequest(table_name="invoices"))
    assert sorted(row["title"] for row in rows["records"]) == ["inv-1", "inv-2"]
    public = await pod_get_records(ctx, PodGetRecordsRequest(table_name="notices"))
    assert [row["title"] for row in public["records"]] == ["closed friday"]
    for refused in ("payroll", "mine"):
        denied = await pod_get_records(ctx, PodGetRecordsRequest(table_name=refused))
        assert denied["success"] is False, (refused, denied)
    queried = await pod_query(ctx, QueryRequest(sql="SELECT title FROM payroll"))
    assert queried["success"] is False, queried
    joined = await pod_query(
        ctx,
        QueryRequest(sql="SELECT i.title FROM invoices i JOIN payroll p ON true"),
    )
    assert joined["success"] is False, joined


async def _shared_ledger(client, org) -> tuple[str, str]:
    """Billing's per-user ``ledger``, holding one member's row, shared with Support."""
    support = await _create_test_pod(client, org)
    billing = await _create_test_pod(client, org)
    await create_table(client, billing, "ledger", enable_rls=True)
    await add_rows(client, billing, "ledger", "the admin's own row")
    shared = await connect(
        client, answering=billing, asking=support, shares=[table_share("ledger")]
    )
    assert shared.status_code == status.HTTP_204_NO_CONTENT, shared.text
    # The member who wrote the row still sees it: the table is not empty.
    own = await client.get(f"/pods/{billing}/datastore/tables/ledger/records")
    assert own.status_code == status.HTTP_200_OK, own.text
    assert [row["title"] for row in own.json()["items"]] == ["the admin's own row"]
    return support, billing


@pytest.mark.asyncio
async def test_a_query_on_a_shared_per_user_table_shows_another_pod_no_ones_rows(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """Row security reads a pod as an outsider: no member's per-user rows.

    ``ledger`` is per-user (``enable_rls``) and shared with Support. The grant
    holds -- the query is allowed -- but every row belongs to a member, and a
    pod is no member, so it sees none: not the steward's, not everybody's.
    """
    support, billing = await _shared_ledger(authenticated_client, fixed_test_org)
    ctx = _link_run(
        user_id=fixed_test_user["id"],
        org_id=fixed_test_org["id"],
        answering=billing,
        asking=support,
    )
    queried = await pod_query(ctx, QueryRequest(sql="SELECT title FROM ledger"))
    assert queried["success"] is True, queried
    assert queried["rows"] == []


@pytest.mark.asyncio
async def test_records_of_a_shared_per_user_table_are_an_empty_page_for_another_pod(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _shared_ledger(authenticated_client, fixed_test_org)
    ctx = _link_run(
        user_id=fixed_test_user["id"],
        org_id=fixed_test_org["id"],
        answering=billing,
        asking=support,
    )
    records = await pod_get_records(ctx, PodGetRecordsRequest(table_name="ledger"))
    assert records["success"] is True, records
    assert records["records"] == []


@pytest.mark.asyncio
async def test_a_shared_contact_owned_table_gives_another_pod_no_contacts_rows(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """A pod is an outsider to a contact-owned table, as any outsider is.

    Such a table can never be Public (``contact_owned_tables``), so the only
    way another pod could reach one is a share. Its record tools are refused
    outright, and a query reads under the contact policy as nobody.
    """
    client = authenticated_client
    support = await _create_test_pod(client, fixed_test_org)
    billing = await _create_test_pod(client, fixed_test_org)
    made = await client.post(
        f"/pods/{billing}/datastore/tables",
        json={
            "name": "tickets",
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["subject"],
            "columns": [{"name": "subject", "type": "TEXT", "required": True}],
        },
    )
    assert made.status_code == status.HTTP_201_CREATED, made.text
    row = await client.post(
        f"/pods/{billing}/datastore/tables/tickets/records",
        json={"data": {"subject": "refund for order 7", "contact_id": str(uuid4())}},
    )
    assert row.status_code == status.HTTP_201_CREATED, row.text
    shared = await connect(
        client, answering=billing, asking=support, shares=[table_share("tickets")]
    )
    assert shared.status_code == status.HTTP_204_NO_CONTENT, shared.text

    ctx = _link_run(
        user_id=fixed_test_user["id"],
        org_id=fixed_test_org["id"],
        answering=billing,
        asking=support,
    )
    records = await pod_get_records(ctx, PodGetRecordsRequest(table_name="tickets"))
    assert (records["success"], records["code"]) == (False, "DATASTORE_ACCESS_DENIED")
    queried = await pod_query(ctx, QueryRequest(sql="SELECT subject FROM tickets"))
    assert queried["success"] is True, queried
    assert queried["rows"] == []


@pytest.mark.asyncio
async def test_the_outgoing_links_index_the_model_declares_is_the_one_migrated():
    """``ix_rpg_pod_grantee``: model and migration 0050 must say the same index."""
    declared = next(
        index
        for index in ResourcePermissionGrantModel.__table__.indexes
        if index.name == "ix_rpg_pod_grantee"
    )
    assert [column.name for column in declared.columns] == [
        "grantee_id",
        "permission_id",
    ]
    assert "grantee_type = 'POD'" in str(
        declared.dialect_options["postgresql"]["where"]
    )
    async with async_session_maker() as session:
        definition = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'resource_permission_grants' "
                "AND indexname = 'ix_rpg_pod_grantee'"
            )
        )
    assert definition is not None
    assert "(grantee_id, permission_id)" in definition
    assert "WHERE ((grantee_type)::text = 'POD'::text)" in definition
