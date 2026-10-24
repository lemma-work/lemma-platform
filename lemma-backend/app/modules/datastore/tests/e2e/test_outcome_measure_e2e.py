"""E2E: what a pod owns is one number for everyone, with the weeks behind it.

A measure is read over every row of the tables it names, so two members of a
pod see the same number even where row security shows each of them only their
own rows. Only someone who can read every one of those rows may set it. Its
reading is kept as the week's, by the work item that setting it writes, and the
history is those weeks.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status
from sqlalchemy import text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.datastore.events.outcome_week_sweep import queue_outcome_weeks
from app.modules.datastore.events.outcome_week_work import outcome_week_key
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


def _owns(measure: str, **extra: object) -> dict[str, object]:
    return {
        "sentence": "Every lead gets a follow-up within two days.",
        "kind": "week",
        "target": 95,
        "unit": "%",
        "measure": measure,
        **extra,
    }


async def _private_leads(pod_api: DatastoreApi) -> str:
    """A table with row security: each member reads only the rows they wrote."""
    name = f"leads_{uuid4().hex[:8]}"
    await pod_api.create_table(
        {
            "name": name,
            "enable_rls": True,
            "columns": [
                {"name": "title", "type": "TEXT"},
                {"name": "followed_up", "type": "BOOLEAN"},
            ],
        }
    )
    return name


async def _put(api: DatastoreApi, body: dict[str, object], expected: int) -> dict:
    response = await api.request("PUT", f"/pods/{api.pod_id}/outcome", json=body)
    assert response.status_code == expected, response.text
    return response.json() if response.content else {}


async def _get(
    api: DatastoreApi, path: str, expected: int = status.HTTP_200_OK
) -> dict:
    response = await api.request("GET", f"/pods/{api.pod_id}/outcome/{path}")
    assert response.status_code == expected, response.text
    return response.json()


async def _queued_readings(pod_id: str) -> int:
    async with async_session_maker() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM work_items WHERE dedup_key = :key"),
                {"key": outcome_week_key(UUID(pod_id))},
            )
            or 0
        )


class TestOneNumberForEveryone:
    @pytest.mark.asyncio
    async def test_members_who_see_different_rows_see_the_same_number(
        self, pod_api: DatastoreApi, async_client, member_users
    ):
        editor = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        table = await _private_leads(pod_api)
        await pod_api.create_record(table, {"title": "owner's", "followed_up": True})
        await editor.create_record(table, {"title": "editor's", "followed_up": False})
        measure = (
            f"SELECT round(100.0 * count(*) FILTER (WHERE followed_up) / "
            f"nullif(count(*), 0), 1) FROM {table}"
        )

        saved = await _put(pod_api, _owns(measure), status.HTTP_200_OK)
        assert saved["value"] == 50.0, "read over both rows when it was set"

        as_owner = await _get(pod_api, "standing")
        as_editor = await _get(editor, "standing")
        assert as_owner["value"] == as_editor["value"] == 50.0
        # The editor's own ad-hoc read still shows them only their own row.
        own = await editor.query(f"SELECT count(*) AS n FROM {table}")
        assert own["items"][0]["n"] == 1

    @pytest.mark.asyncio
    async def test_a_viewer_reads_the_number_and_cannot_set_it(
        self, pod_api: DatastoreApi, async_client, member_users
    ):
        viewer = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
        table = await _private_leads(pod_api)
        await _put(pod_api, _owns(f"SELECT count(*) FROM {table}"), status.HTTP_200_OK)
        assert (await _get(viewer, "standing"))["outcome"]["kind"] == "week"
        assert len((await _get(viewer, "history"))["weeks"]) == 8
        await _put(
            viewer, _owns(f"SELECT count(*) FROM {table}"), status.HTTP_403_FORBIDDEN
        )


class TestWhoMaySetAMeasure:
    @pytest.mark.asyncio
    async def test_an_editor_who_sees_only_their_rows_cannot_count_everyones(
        self, pod_api: DatastoreApi, async_client, member_users
    ):
        editor = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        table = await _private_leads(pod_api)
        refused = await _put(
            editor, _owns(f"SELECT count(*) FROM {table}"), status.HTTP_403_FORBIDDEN
        )
        assert refused["code"] == "DATASTORE_ACCESS_DENIED"
        assert (await _get(pod_api, "standing"))["outcome"] is None

    @pytest.mark.asyncio
    async def test_a_table_hidden_behind_a_cte_of_its_name_is_still_checked(
        self, pod_api: DatastoreApi, async_client, member_users
    ):
        editor = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        secret = await _private_leads(pod_api)
        await pod_api.create_record(secret, {"title": "owner's"})
        mine = f"mine_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": mine,
                "enable_rls": False,
                "columns": [{"name": "title", "type": "TEXT"}],
            }
        )
        await pod_api.create_record(mine, {"title": "anyone's"})
        shadowed = (
            f"SELECT count(*) FROM {mine} WHERE (SELECT count(*) FROM {secret}) > 0 "
            f"AND EXISTS (WITH {secret} AS (SELECT 1) SELECT 1 FROM {secret})"
        )
        refused = await _put(editor, _owns(shadowed), status.HTTP_403_FORBIDDEN)
        assert refused["code"] == "DATASTORE_ACCESS_DENIED"
        assert secret in refused["message"]

    @pytest.mark.asyncio
    async def test_the_same_editor_may_move_the_target_of_a_measure_already_set(
        self, pod_api: DatastoreApi, async_client, member_users
    ):
        editor = DatastoreApi(async_client, pod_api.pod_id, member_users["editor"])
        table = await _private_leads(pod_api)
        measure = f"SELECT count(*) FROM {table}"
        await _put(pod_api, _owns(measure), status.HTTP_200_OK)
        moved = await _put(editor, _owns(measure, target=80), status.HTTP_200_OK)
        assert moved["outcome"]["target"] == 80 and moved["value"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("measure", "expected", "code"),
        [
            ("SELECT count(*) FROM no_such_table", 404, "DATASTORE_TABLE_NOT_FOUND"),
            ("SELECT 1; DROP TABLE x", 400, "DATASTORE_QUERY_ERROR"),
            ("SELECT 1", 400, "DATASTORE_QUERY_ERROR"),
        ],
    )
    async def test_a_measure_that_is_not_one_query_over_its_tables_is_refused(
        self, pod_api: DatastoreApi, measure, expected, code
    ):
        refused = await _put(pod_api, _owns(measure), expected)
        assert refused["code"] == code

    @pytest.mark.asyncio
    async def test_a_measure_that_does_not_answer_one_number_is_refused(
        self, pod_api: DatastoreApi
    ):
        table = await _private_leads(pod_api)
        await pod_api.create_record(table, {"title": "a"})
        await pod_api.create_record(table, {"title": "b"})
        refused = await _put(
            pod_api,
            _owns(f"SELECT title FROM {table}"),
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
        assert refused["code"] == "DATASTORE_MEASURE_INVALID"
        assert "2 rows" in refused["message"]


@pytest.mark.drives_work
class TestTheWeeksBehindIt:
    """Drives the reading items in-process, so the session worker is held."""

    @pytest.mark.asyncio
    async def test_setting_a_measure_reads_it_into_this_week(
        self, pod_api: DatastoreApi
    ):
        table = await _private_leads(pod_api)
        await pod_api.create_record(table, {"title": "a", "followed_up": True})
        await _put(pod_api, _owns(f"SELECT count(*) FROM {table}"), status.HTTP_200_OK)
        assert await _queued_readings(pod_api.pod_id) == 1

        await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])

        weeks = (await _get(pod_api, "history"))["weeks"]
        assert len(weeks) == 8
        assert weeks[-1]["value"] == 1 and weeks[-1]["target"] == 95
        assert all(week["value"] is None for week in weeks[:-1])

    @pytest.mark.asyncio
    async def test_the_sweep_rereads_the_week_and_a_new_measure_starts_over(
        self, pod_api: DatastoreApi
    ):
        table = await _private_leads(pod_api)
        await pod_api.create_record(table, {"title": "a"})
        counted = f"SELECT count(*) FROM {table}"
        await _put(pod_api, _owns(counted), status.HTTP_200_OK)
        await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
        await pod_api.create_record(table, {"title": "b"})

        assert await queue_outcome_weeks() >= 1
        await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
        assert (await _get(pod_api, "history"))["weeks"][-1]["value"] == 2

        followed = f"SELECT count(*) FROM {table} WHERE followed_up"
        await _put(pod_api, _owns(followed), status.HTTP_200_OK)
        assert (await _get(pod_api, "history"))["weeks"][-1]["value"] is None

    @pytest.mark.asyncio
    async def test_a_table_dropped_under_its_measure_is_a_problem_not_an_error(
        self, pod_api: DatastoreApi
    ):
        table = await _private_leads(pod_api)
        await pod_api.create_record(table, {"title": "a"})
        await _put(pod_api, _owns(f"SELECT count(*) FROM {table}"), status.HTTP_200_OK)
        await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
        await pod_api.delete_table(table)

        standing = await _get(pod_api, "standing")
        assert standing["value"] is None
        assert (
            standing["problem"]
            == "The measure counts a table that is not here any more."
        )

        await queue_outcome_weeks()
        await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
        week = (await _get(pod_api, "history"))["weeks"][-1]
        assert week["value"] == 1, "the week keeps the number it last read"
        assert week["problem"] == standing["problem"]
