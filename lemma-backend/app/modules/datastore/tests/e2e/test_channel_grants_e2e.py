"""A table given to a group -- against a real database.

A group's people outside the pod read only what is Public, until a member
gives the group a table. Each test pins one rule:

* given, the group reads the given columns -- no others, and no filtering on
  a column it was not given, which would tell it what that column holds;
* given adding, a guest's row lands, written as the granting member and
  marked as from outside -- and one carrying a value it may not fill is
  refused whole;
* a read cut short says how many rows matched;
* a column whose refusal would tell what the table holds is never filled;
* a table where each member sees only their own rows is not given -- nor
  read once it is made one;
* a table nobody gave the group is the same refusal as one that does not
  exist.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.datastore.contracts.channel_grants import (
    PublicRowRefused,
    PublicRowsClosed,
    add_granted_row,
    read_granted_rows,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e

GUEST = UUID(int=21)


def _uow_factory(db_session: AsyncSession) -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(
        async_sessionmaker(db_session.bind, expire_on_commit=False)
    )


async def _group(pod_api: DatastoreApi) -> str:
    made = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/channels",
        json={"title": f"trip-{uuid4().hex[:6]}"},
    )
    assert made.status_code in (200, 201), made.text
    return made.json()["id"]


async def _ledger(pod_api: DatastoreApi, *, rls: bool = False) -> str:
    name = f"ledger_{uuid4().hex[:8]}"
    await pod_api.create_table(
        {
            "name": name,
            "enable_rls": rls,
            "columns": [
                {"name": "what", "type": "TEXT", "required": True},
                {"name": "amount", "type": "FLOAT"},
                {"name": "secret_note", "type": "TEXT"},
            ],
        }
    )
    return name


async def _grant(pod_api: DatastoreApi, channel_id: str, table: str, **body):
    return await pod_api.request(
        "PUT",
        f"/pods/{pod_api.pod_id}/channels/{channel_id}/table-grants/{table}",
        json=body,
    )


async def test_a_group_reads_only_the_columns_it_was_given(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api)
    await pod_api.create_record(
        table, {"what": "breakfast", "amount": 2248, "secret_note": "hidden"}
    )
    granted = await _grant(
        pod_api, channel_id, table, read_columns=["what", "amount"], add_columns=[]
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text

    rows = await read_granted_rows(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        channel_id=UUID(channel_id),
        table_name=table,
        equals={"what": "breakfast"},
        contact_id=GUEST,
    )

    assert (rows.rows, rows.total) == ([{"what": "breakfast", "amount": 2248}], 1)
    with pytest.raises(PublicRowsClosed):
        await read_granted_rows(
            _uow_factory(db_session),
            pod_id=UUID(pod_api.pod_id),
            channel_id=UUID(channel_id),
            table_name=table,
            equals={"secret_note": "hidden"},
            contact_id=GUEST,
        )
    listed = await pod_api.request(
        "GET", f"/pods/{pod_api.pod_id}/channels/{channel_id}/table-grants"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert [item["table"] for item in listed.json()["items"]] == [table]


async def test_a_guests_row_lands_as_the_granting_member(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api)
    granted = await _grant(
        pod_api,
        channel_id,
        table,
        read_columns=["what", "amount"],
        add_columns=["what", "amount"],
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text

    await add_granted_row(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        channel_id=UUID(channel_id),
        table_name=table,
        answers={"what": "lunch", "amount": 2240},
        contact_id=GUEST,
    )

    rows = await read_granted_rows(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        channel_id=UUID(channel_id),
        table_name=table,
        equals={},
        contact_id=GUEST,
    )
    assert {"what": "lunch", "amount": 2240.0} in rows.rows

    # A value for a column the group may not fill refuses the row: saved
    # without it, it would not be what the guest said.
    with pytest.raises(PublicRowRefused):
        await add_granted_row(
            _uow_factory(db_session),
            pod_id=UUID(pod_api.pod_id),
            channel_id=UUID(channel_id),
            table_name=table,
            answers={"what": "dinner", "amount": 10, "secret_note": "x"},
            contact_id=GUEST,
        )
    after = await read_granted_rows(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        channel_id=UUID(channel_id),
        table_name=table,
        equals={"what": "dinner"},
        contact_id=GUEST,
    )
    assert after.rows == []


async def test_a_read_cut_short_says_how_many_matched(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api)
    for amount in (1, 2, 3):
        await pod_api.create_record(table, {"what": "fuel", "amount": amount})
    granted = await _grant(pod_api, channel_id, table, read_columns=["what", "amount"])
    assert granted.status_code == status.HTTP_200_OK, granted.text

    found = await read_granted_rows(
        _uow_factory(db_session),
        pod_id=UUID(pod_api.pod_id),
        channel_id=UUID(channel_id),
        table_name=table,
        equals={"what": "fuel"},
        contact_id=GUEST,
        limit=2,
    )

    assert (len(found.rows), found.total) == (2, 3)


async def test_a_column_whose_refusal_would_tell_is_not_one_a_guest_fills(
    pod_api: DatastoreApi,
):
    """A unique column refuses a value somebody already gave -- which would
    tell the group the value is in the table."""
    channel_id = await _group(pod_api)
    name = f"guests_{uuid4().hex[:8]}"
    await pod_api.create_table(
        {
            "name": name,
            "enable_rls": False,
            "columns": [
                {"name": "email", "type": "TEXT", "required": True, "unique": True},
                {"name": "note", "type": "TEXT"},
            ],
        }
    )

    refused = await _grant(
        pod_api, channel_id, name, read_columns=["note"], add_columns=["email"]
    )

    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "must be unique" in refused.text


async def test_a_table_each_member_reads_alone_is_not_given(pod_api: DatastoreApi):
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api, rls=True)

    refused = await _grant(pod_api, channel_id, table, read_columns=["what"])

    assert refused.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "only their own rows" in refused.text


async def test_a_table_made_per_user_after_it_was_given_closes_to_the_group(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    """Given while everybody read every row, then made per-user: its rows are
    now each member's own, and the granter's are not the group's to read."""
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api)
    await pod_api.create_record(table, {"what": "granter's own", "amount": 1})
    granted = await _grant(
        pod_api, channel_id, table, read_columns=["what"], add_columns=["what"]
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text

    await pod_api.update_table(table, {"enable_rls": True})

    for attempt in (
        read_granted_rows(
            _uow_factory(db_session),
            pod_id=UUID(pod_api.pod_id),
            channel_id=UUID(channel_id),
            table_name=table,
            equals={},
            contact_id=GUEST,
        ),
        add_granted_row(
            _uow_factory(db_session),
            pod_id=UUID(pod_api.pod_id),
            channel_id=UUID(channel_id),
            table_name=table,
            answers={"what": "lunch"},
            contact_id=GUEST,
        ),
    ):
        with pytest.raises(PublicRowsClosed):
            await attempt


async def test_a_table_nobody_gave_the_group_is_not_there(
    pod_api: DatastoreApi, db_session: AsyncSession
):
    channel_id = await _group(pod_api)
    table = await _ledger(pod_api)

    with pytest.raises(PublicRowsClosed):
        await add_granted_row(
            _uow_factory(db_session),
            pod_id=UUID(pod_api.pod_id),
            channel_id=UUID(channel_id),
            table_name=table,
            answers={"what": "lunch"},
            contact_id=GUEST,
        )
    removed = await pod_api.request(
        "DELETE",
        f"/pods/{pod_api.pod_id}/channels/{channel_id}/table-grants/{table}",
    )
    assert removed.status_code == status.HTTP_204_NO_CONTENT
