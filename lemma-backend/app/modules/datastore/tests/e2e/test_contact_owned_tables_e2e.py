"""Contact-owned tables against a real pod database.

What each principal sees is decided by the database, under the NOBYPASSRLS
query role, from the settings the session names: a member every row, a contact
their own, nobody else nothing -- and a session that named nobody, nothing at
all. The rest is what may be asked of such a table: never Public, never both
per-user and contact-owned whatever order a PATCH says it in, and read by a contact only through the columns a member chose.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.authorization.anonymous import build_outsider_context
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.datastore.contracts.contact_rows import rows_for_contact
from app.core.domain.errors import DomainError
from app.modules.datastore.domain.row_security import RowPrincipal
from app.modules.datastore.infrastructure.readonly_query import (
    execute_readonly_query,
)
from app.modules.datastore.services.wiring import (
    build_record_service,
    build_table_service,
    get_schema_manager,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.row_bound_records import row_bound_records

pytestmark = pytest.mark.e2e

MINE, THEIRS = UUID(int=1), UUID(int=2)


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


async def _tickets(pod_api: DatastoreApi, **extra) -> str:
    name = extra.pop("name", None) or _name("tickets")
    await pod_api.create_table(
        {
            "name": name,
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["subject"],
            "columns": [
                {"name": "subject", "type": "TEXT", "required": True},
                {"name": "cost_price", "type": "FLOAT"},
            ],
            **extra,
        }
    )
    return name


async def _row_security(pod_id: str, table: str) -> tuple[bool, bool, set[str]]:
    schema = get_schema_manager()
    schema_name = schema.get_schema_name(UUID(pod_id))
    async with schema.session_factory() as session:
        flags = (
            await session.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND c.relname = :table"
                ),
                {"schema": schema_name, "table": table},
            )
        ).one()
        policies = await session.scalars(
            text(
                "SELECT policyname FROM pg_policies "
                "WHERE schemaname = :schema AND tablename = :table"
            ),
            {"schema": schema_name, "table": table},
        )
        return bool(flags[0]), bool(flags[1]), set(policies)


def _uow_factory(db_session: AsyncSession) -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )


async def test_a_contact_owned_table_is_never_public(pod_api: DatastoreApi):
    await pod_api.create_table(
        {
            "name": _name("public_tickets"),
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["subject"],
            "visibility": "PUBLIC",
            "columns": [{"name": "subject", "type": "TEXT"}],
        },
        expected_status=status.HTTP_400_BAD_REQUEST,
    )
    owned = await _tickets(pod_api)
    await pod_api.update_table(
        owned, {"visibility": "PUBLIC"}, expected_status=status.HTTP_400_BAD_REQUEST
    )
    plain = _name("plain")
    await pod_api.create_table(
        {
            "name": plain,
            "enable_rls": False,
            "visibility": "PUBLIC",
            "columns": [{"name": "subject", "type": "TEXT"}],
        }
    )
    await pod_api.update_table(
        plain,
        {"contact_owned": True, "contact_columns": ["subject"]},
        expected_status=status.HTTP_400_BAD_REQUEST,
    )
    assert (await pod_api.get_table(owned))["visibility"] != "PUBLIC"


async def test_a_contact_owned_table_needs_the_columns_contacts_may_read(
    pod_api: DatastoreApi,
):
    base = {
        "enable_rls": False,
        "contact_owned": True,
        "columns": [{"name": "subject", "type": "TEXT"}],
    }
    await pod_api.create_table(
        {"name": _name("unchosen"), **base},
        expected_status=status.HTTP_400_BAD_REQUEST,
    )
    await pod_api.create_table(
        {"name": _name("unknown"), **base, "contact_columns": ["nope"]},
        expected_status=status.HTTP_400_BAD_REQUEST,
    )


async def test_each_principal_reads_its_own_share_of_a_contact_owned_table(
    pod_api: DatastoreApi, fixed_test_org, db_session: AsyncSession
):
    table = await _tickets(pod_api)
    for contact, subject in ((MINE, "Mine"), (THEIRS, "Theirs"), (None, "Nobody's")):
        await pod_api.create_record(
            table,
            {"subject": subject, "contact_id": str(contact) if contact else None},
        )
    sql = f'SELECT subject FROM "{table}" ORDER BY subject'

    member = await pod_api.query(sql)
    assert [row["subject"] for row in member["items"]] == ["Mine", "Nobody's", "Theirs"]

    pod_id = UUID(pod_api.pod_id)
    org_id = UUID(fixed_test_org["id"])
    contact_ctx = build_outsider_context(
        session=db_session, pod_id=pod_id, organization_id=org_id, contact_id=MINE
    )
    anonymous_ctx = build_outsider_context(
        session=db_session,
        pod_id=pod_id,
        organization_id=org_id,
        contact_id=None,
        actor_id="visitor:e2e",
    )
    schema = get_schema_manager()

    async def rows_as(principal: RowPrincipal) -> list[str]:
        rows, _count, _truncated = await execute_readonly_query(
            schema, pod_id, sql, principal
        )
        return [row["subject"] for row in rows]

    assert await rows_as(RowPrincipal.of(contact_ctx)) == ["Mine"]
    assert await rows_as(RowPrincipal.of(anonymous_ctx)) == []

    # The table is not Public, so neither outsider is let as far as the query.
    async with _uow_factory(db_session)() as uow:
        for ctx in (contact_ctx, anonymous_ctx):
            with pytest.raises(DomainError):
                await build_record_service(uow).execute_readonly_query(
                    pod_id=pod_id,
                    query=sql,
                    user_id=None,
                    table_service=build_table_service(uow),
                    ctx=ctx,
                )


async def test_a_session_that_names_nobody_sees_no_contact_row(pod_api: DatastoreApi):
    """The policy alone fails closed: no settings, no rows -- not every row."""
    from app.modules.datastore.config import datastore_settings

    table = await _tickets(pod_api)
    await pod_api.create_record(table, {"subject": "Mine", "contact_id": str(MINE)})
    schema = get_schema_manager()
    await schema.ensure_query_role()
    schema_name = schema.get_schema_name(UUID(pod_api.pod_id))
    async with schema.session_factory() as session:
        await session.execute(
            text(f'SET LOCAL ROLE "{datastore_settings.datastore_query_role}"')
        )
        rows = (
            await session.execute(text(f'SELECT * FROM "{schema_name}"."{table}"'))
        ).all()
    assert rows == []


async def test_an_anonymous_run_can_query_a_public_table(
    pod_api: DatastoreApi, fixed_test_org, db_session: AsyncSession
):
    """Once, writing the nil UUID and expecting "None" discarded every such query."""
    table = _name("price_list")
    await pod_api.create_table(
        {
            "name": table,
            "enable_rls": False,
            "visibility": "PUBLIC",
            "columns": [{"name": "item", "type": "TEXT"}],
        }
    )
    await pod_api.create_record(table, {"item": "Kettle"})
    pod_id = UUID(pod_api.pod_id)
    async with _uow_factory(db_session)() as uow:
        ctx = build_outsider_context(
            session=uow.session,
            pod_id=pod_id,
            organization_id=UUID(fixed_test_org["id"]),
            contact_id=None,
            actor_id="visitor:e2e",
        )
        rows, count, truncated = await build_record_service(uow).execute_readonly_query(
            pod_id=pod_id,
            query=f'SELECT item FROM "{table}"',
            user_id=None,
            table_service=build_table_service(uow),
            ctx=ctx,
        )
    assert (rows, count, truncated) == ([{"item": "Kettle"}], 1, False)


async def test_swapping_contact_ownership_for_per_user_keeps_row_security_on(
    pod_api: DatastoreApi,
):
    table = await _tickets(pod_api)
    assert await _row_security(pod_api.pod_id, table) == (
        True,
        True,
        {f"{table}_contact_isolation"},
    )

    swapped = await pod_api.update_table(
        table, {"enable_rls": True, "contact_owned": False}
    )
    assert (swapped["enable_rls"], swapped["contact_owned"]) == (True, False)
    assert await _row_security(pod_api.pod_id, table) == (
        True,
        True,
        {f"{table}_user_isolation"},
    )

    back = await pod_api.update_table(
        table,
        {"enable_rls": False, "contact_owned": True, "contact_columns": ["subject"]},
    )
    assert (back["enable_rls"], back["contact_owned"]) == (False, True)
    assert await _row_security(pod_api.pod_id, table) == (
        True,
        True,
        {f"{table}_contact_isolation"},
    )


async def test_a_contact_reads_only_the_chosen_columns_in_key_order(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    table = await _tickets(pod_api)
    for subject in ("First", "Second", "Third"):
        await pod_api.create_record(
            table, {"subject": subject, "cost_price": 9.5, "contact_id": str(MINE)}
        )
    await pod_api.create_record(
        table, {"subject": "Theirs", "cost_price": 1.0, "contact_id": str(THEIRS)}
    )
    member_rows = (await pod_api.list_records(table))["items"]
    by_key = sorted(member_rows, key=lambda row: str(row["id"]))

    rows = await rows_for_contact(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        table_name=table,
        contact_id=MINE,
    )
    assert rows == [
        {"subject": row["subject"]} for row in by_key if row["subject"] != "Theirs"
    ]


async def test_a_member_works_a_contact_owned_table_where_row_security_binds(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    """Every record call names the member, so every contact's row is theirs.

    Under a role the policy binds, as a deployment's is. Once these named
    nobody on a table that was not per-user, and the policy refused every
    write and matched no row.
    """
    table = await _tickets(pod_api)
    async with row_bound_records(UUID(pod_api.pod_id)):
        mine = await pod_api.create_record(
            table, {"subject": "Mine", "contact_id": str(MINE)}
        )
        theirs = await pod_api.create_record(
            table, {"subject": "Theirs", "contact_id": str(THEIRS)}
        )
        assert (await pod_api.get_record(table, mine["id"]))["subject"] == "Mine"
        updated = await pod_api.update_record(table, theirs["id"], {"cost_price": 2.5})
        assert updated["cost_price"] == 2.5

        await pod_api.bulk_create(
            table,
            [
                {"subject": "Second", "contact_id": str(MINE)},
                {"subject": "Unclaimed"},
            ],
        )
        listed = (await pod_api.list_records(table))["items"]
        by_subject = {row["subject"]: row for row in listed}
        assert sorted(by_subject) == ["Mine", "Second", "Theirs", "Unclaimed"]
        await pod_api.bulk_update(
            table,
            [{"id": by_subject[name]["id"], "cost_price": 1.0} for name in by_subject],
        )

        await pod_api.delete_record(table, theirs["id"])
        await pod_api.bulk_delete(table, [by_subject["Unclaimed"]["id"]])
        left = (await pod_api.list_records(table))["items"]
        assert sorted(row["subject"] for row in left) == ["Mine", "Second"]
        assert {row["cost_price"] for row in left} == {1.0}

        async def subjects_of(contact_id: UUID) -> list[str]:
            rows = await rows_for_contact(
                _uow_factory(db_session),
                pod_id=UUID(pod_api.pod_id),
                table_name=table,
                contact_id=contact_id,
            )
            return sorted(str(row["subject"]) for row in rows)

        assert await subjects_of(MINE) == ["Mine", "Second"]
        assert await subjects_of(THEIRS) == []


async def test_per_user_rows_keep_to_their_writer_where_row_security_binds(
    pod_api: DatastoreApi, member_users, async_client
):
    """What the contact-owned fix must not move: a per-user row is its writer's."""
    table = _name("notes")
    await pod_api.create_table(
        {
            "name": table,
            "enable_rls": True,
            "columns": [{"name": "body", "type": "TEXT"}],
        }
    )
    editor = DatastoreApi(async_client, pod_api.pod_id, user=member_users["editor"])
    async with row_bound_records(UUID(pod_api.pod_id)):
        mine = await pod_api.create_record(table, {"body": "mine"})
        theirs = await editor.create_record(table, {"body": "theirs"})
        assert [row["id"] for row in (await pod_api.list_records(table))["items"]] == [
            mine["id"]
        ]
        await pod_api.get_record(
            table, theirs["id"], expected_status=status.HTTP_404_NOT_FOUND
        )
        everyone = await pod_api.list_records(table, mode="ADMIN")
        assert {row["id"] for row in everyone["items"]} == {mine["id"], theirs["id"]}
