"""E2E: `table.column.update` widens an ENUM column in PostgreSQL itself.

The record API refuses a value outside the declared options before it reaches
the database, so a passing record write alone would not show the CHECK
changed. These also read the catalog and write past the API to show the
column's one CHECK is the new one and still refuses everything else.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


def _relation(pod_api: DatastoreApi, table_name: str) -> str:
    return f'"pod_{pod_api.pod_id.replace("-", "_")}"."{table_name}"'


async def _create_tickets(pod_api: DatastoreApi) -> str:
    table_name = f"tickets_{uuid4().hex[:8]}"
    await pod_api.create_table(
        {
            "name": table_name,
            "enable_rls": False,
            "columns": [
                {"name": "title", "type": "TEXT"},
                {
                    "name": "status",
                    "type": "ENUM",
                    "required": True,
                    "options": ["open", "closed"],
                },
            ],
        }
    )
    await pod_api.create_record(table_name, {"title": "first", "status": "closed"})
    return table_name


async def _update_column(
    api: DatastoreApi,
    table_name: str,
    column_name: str,
    payload: dict[str, object],
    *,
    expected_status: int = status.HTTP_200_OK,
) -> dict[str, object]:
    response = await api.request(
        "PATCH",
        f"/pods/{api.pod_id}/datastore/tables/{table_name}/columns/{column_name}",
        json=payload,
    )
    assert response.status_code == expected_status, response.text
    return response.json() if response.content else {}


async def _options(pod_api: DatastoreApi, table_name: str) -> list[str]:
    table = await pod_api.get_table(table_name)
    return next(c["options"] for c in table["columns"] if c["name"] == "status")


class TestWideningAnEnumColumn:
    @pytest.mark.asyncio
    async def test_a_widened_column_takes_the_new_option_and_refuses_the_rest(
        self, pod_api: DatastoreApi
    ):
        table_name = await _create_tickets(pod_api)

        updated = await _update_column(
            pod_api, table_name, "status", {"options": ["open", "closed", "waiting"]}
        )

        status_column = next(c for c in updated["columns"] if c["name"] == "status")
        assert status_column["options"] == ["open", "closed", "waiting"]
        assert status_column["required"] is True
        assert await _options(pod_api, table_name) == ["open", "closed", "waiting"]

        await pod_api.create_record(table_name, {"title": "new", "status": "waiting"})
        await pod_api.create_record(
            table_name,
            {"title": "stray", "status": "lost"},
            expected_status=status.HTTP_400_BAD_REQUEST,
        )

        relation = _relation(pod_api, table_name)
        async with get_datastore_engine().connect() as conn:
            checks = await conn.execute(
                text(
                    "SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
                    "JOIN pg_attribute att ON att.attrelid = con.conrelid "
                    "AND att.attname = 'status' "
                    "WHERE con.contype = 'c' "
                    "AND con.conrelid = to_regclass(:relation) "
                    "AND con.conkey = ARRAY[att.attnum]"
                ),
                {"relation": relation},
            )
            definitions = checks.scalars().all()
        assert len(definitions) == 1, definitions
        assert "'waiting'" in definitions[0]

        with pytest.raises(IntegrityError):
            async with get_datastore_engine().begin() as conn:
                await conn.execute(
                    text(
                        f"INSERT INTO {relation} (title, status) "
                        "VALUES ('past the API', 'lost')"
                    )
                )

    @pytest.mark.asyncio
    async def test_narrowing_is_refused_and_changes_nothing(
        self, pod_api: DatastoreApi
    ):
        table_name = await _create_tickets(pod_api)

        refused = await _update_column(
            pod_api,
            table_name,
            "status",
            {"options": ["open", "waiting"]},
            expected_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

        assert refused["code"] == "DATASTORE_COLUMN_CHANGE_REFUSED"
        assert "'closed'" in refused["message"]
        assert await _options(pod_api, table_name) == ["open", "closed"]
        await pod_api.create_record(table_name, {"title": "again", "status": "closed"})
        await pod_api.create_record(
            table_name,
            {"title": "stray", "status": "waiting"},
            expected_status=status.HTTP_400_BAD_REQUEST,
        )

    @pytest.mark.asyncio
    async def test_what_it_does_not_change_is_said_rather_than_ignored(
        self, pod_api: DatastoreApi
    ):
        table_name = await _create_tickets(pod_api)

        same = await _update_column(
            pod_api, table_name, "status", {"options": ["open", "closed"]}
        )
        assert next(c for c in same["columns"] if c["name"] == "status")["options"] == [
            "open",
            "closed",
        ]

        not_enum = await _update_column(
            pod_api,
            table_name,
            "title",
            {"options": ["a", "b"]},
            expected_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        assert not_enum["code"] == "DATASTORE_COLUMN_CHANGE_REFUSED"

        await _update_column(
            pod_api,
            table_name,
            "status",
            {"options": ["open", "closed", "waiting"], "required": False},
            expected_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        await _update_column(
            pod_api,
            table_name,
            "priority",
            {"options": ["low"]},
            expected_status=status.HTTP_404_NOT_FOUND,
        )
        assert await _options(pod_api, table_name) == ["open", "closed"]

    @pytest.mark.asyncio
    async def test_a_pod_viewer_cannot_change_a_column(
        self,
        pod_api: DatastoreApi,
        async_client: AsyncClient,
        member_users,
    ):
        table_name = await _create_tickets(pod_api)
        viewer_api = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])

        await _update_column(
            viewer_api,
            table_name,
            "status",
            {"options": ["open", "closed", "waiting"]},
            expected_status=status.HTTP_403_FORBIDDEN,
        )
        assert await _options(pod_api, table_name) == ["open", "closed"]


class TestDroppingAForeignKey:
    @pytest.mark.asyncio
    async def test_a_dropped_foreign_key_stops_holding_the_column_to_the_other_table(
        self, pod_api: DatastoreApi
    ):
        """A bundle no longer declares a column's foreign key: the import used
        to report the table updated and keep it."""
        customers = f"customers_{uuid4().hex[:8]}"
        deals = f"deals_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": customers,
                "enable_rls": False,
                "columns": [{"name": "name", "type": "TEXT"}],
            }
        )
        await pod_api.create_table(
            {
                "name": deals,
                "enable_rls": False,
                "columns": [
                    {
                        "name": "customer_id",
                        "type": "UUID",
                        "foreign_key": {"references": f"{customers}.id"},
                    }
                ],
            }
        )
        nobody = str(uuid4())
        await pod_api.create_record(
            deals,
            {"customer_id": nobody},
            expected_status=status.HTTP_400_BAD_REQUEST,
        )

        updated = await _update_column(
            pod_api, deals, "customer_id", {"drop_foreign_key": True}
        )

        column = next(c for c in updated["columns"] if c["name"] == "customer_id")
        assert column.get("foreign_key") is None
        await pod_api.create_record(deals, {"customer_id": nobody})
        # A second ask, and a column that never had one, change nothing.
        await _update_column(pod_api, deals, "customer_id", {"drop_foreign_key": True})
        await _update_column(
            pod_api,
            deals,
            "customer_id",
            {},
            expected_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
