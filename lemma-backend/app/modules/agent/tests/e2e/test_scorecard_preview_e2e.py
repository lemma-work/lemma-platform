"""E2E: a scorecard's recent weeks and the rows behind its numbers, from real tables.

Every number here is a statement the datastore runs -- a window bound, a test
over a row, a median, row security -- and a fake would hand back whatever the
test gave it. So the units are seeded through the records API, each with the
date that places it in one of the last four weeks, and the routes are called
the way the setup screen calls them.

What each seeded row is for is said beside it; the expected weeks are the sum
of those comments. Weeks are named by how many whole weeks before today they
end: ``w3`` is the week that ended today, ``w0`` the one that ended 21 days ago.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.delegation import is_pod_default_agent
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import TryMeasureRequest
from app.modules.agent.tools.pod.try_measure import try_measure
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

_SCORECARD_COLUMNS = [
    {"name": "id", "type": "UUID", "required": True, "auto": True},
    {"name": "key", "type": "TEXT", "required": True, "unique": True},
    {"name": "measure", "type": "TEXT", "required": True},
    {"name": "kind", "type": "TEXT", "required": True},
    {"name": "counter", "type": "TEXT", "required": True},
    {"name": "query", "type": "TEXT"},
    {"name": "aim", "type": "TEXT", "required": True},
    {"name": "target", "type": "FLOAT", "required": True},
    {"name": "target_label", "type": "TEXT", "required": True},
    {"name": "counted_from", "type": "TEXT", "required": True},
    {"name": "is_on", "type": "BOOLEAN", "required": True},
    {"name": "note", "type": "TEXT"},
    {"name": "position", "type": "INTEGER", "required": True},
    {"name": "shape", "type": "TEXT"},
    {"name": "unit_table", "type": "TEXT"},
    {"name": "time_column", "type": "TEXT"},
    {"name": "test", "type": "TEXT"},
    {"name": "value", "type": "TEXT"},
    {"name": "value_unit", "type": "TEXT"},
    {"name": "label_column", "type": "TEXT"},
    {"name": "link_column", "type": "TEXT"},
    {"name": "proposed_from", "type": "TEXT"},
]

_CALLBACK_COLUMNS = [
    {"name": "id", "type": "UUID", "required": True, "auto": True},
    {"name": "customer", "type": "TEXT", "required": True},
    {"name": "due", "type": "DATE"},
    {"name": "done", "type": "BOOLEAN"},
    {"name": "conversation", "type": "TEXT"},
]

_CONVERSATION_COLUMNS = [
    {"name": "id", "type": "UUID", "required": True, "auto": True},
    {"name": "link", "type": "TEXT"},
    {"name": "customer", "type": "TEXT"},
    {"name": "started_at", "type": "DATETIME", "required": True},
    {"name": "first_reply_at", "type": "DATETIME"},
]

_REPLY_MINUTES = "extract(epoch from first_reply_at - started_at) / 60"
_PER_WEEK_SQL = (
    "SELECT count(*) AS counted, count(*) AS total FROM callbacks "
    "WHERE due >= '{start}'::date AND due < '{end}'::date"
)


def _measure(key: str, position: int, **fields: object) -> dict[str, object]:
    row: dict[str, object] = {
        "key": key,
        "measure": key.replace("_", " "),
        "kind": "outcome",
        "counter": "work",
        "aim": "higher",
        "target": 1,
        "target_label": "every one",
        "counted_from": "e2e",
        "is_on": True,
        "position": position,
    }
    row.update(fields)
    return row


_CALLBACKS_ON_TIME = _measure(
    "callbacks_on_time",
    1,
    shape="share",
    unit_table="callbacks",
    time_column="due",
    test="coalesce(done, false)",
    label_column="customer",
    link_column="conversation",
)
_REPLY_TIME = _measure(
    "reply_time",
    2,
    shape="median",
    unit_table="conversations",
    time_column="started_at",
    value=_REPLY_MINUTES,
    value_unit="minutes",
    aim="lower",
    target=30,
    target_label="30 minutes",
)
_PROPOSED = _measure(
    "callbacks_missed",
    3,
    shape="count",
    unit_table="callbacks",
    time_column="due",
    test="NOT coalesce(done, false)",
    aim="lower",
    target=0,
    is_on=False,
    proposed_from="Tell me how many callbacks we miss",
)
_SWITCHED_OFF = _measure(
    "switched_off",
    4,
    shape="share",
    unit_table="callbacks",
    time_column="due",
    test="true",
    is_on=False,
)
_BY_SQL = _measure(
    "counted_by_sql",
    5,
    counter="sql",
    shape="count",
    query=_PER_WEEK_SQL,
    aim="lower",
    target=10,
)


# --- Setup ----------------------------------------------------------------------


async def _create_pod(client, fixed_test_org) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"scorecard-preview-{uuid4().hex[:8]}",
            "description": "scorecard preview e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(client, pod_id: str, name: str, columns, *, rls=False):
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": name,
            "primary_key_column": "id",
            "enable_rls": rls,
            "columns": columns,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _insert(client, pod_id: str, table: str, rows, **request) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables/{table}/records/bulk/create",
        json={"records": rows},
        **request,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["count"] == len(rows)


def _day(today: date, days_before: int) -> str:
    return (today - timedelta(days=days_before)).isoformat()


def _moment(today: date, days_before: int, *, minutes: int = 0) -> str:
    started = datetime.combine(today, time(10), tzinfo=timezone.utc)
    return (
        started - timedelta(days=days_before) + timedelta(minutes=minutes)
    ).isoformat()


async def _seed(client, fixed_test_org) -> tuple[str, date]:
    today = datetime.now(timezone.utc).date()
    pod_id = await _create_pod(client, fixed_test_org)
    await _create_table(client, pod_id, "scorecard", _SCORECARD_COLUMNS)
    await _insert(
        client,
        pod_id,
        "scorecard",
        [_CALLBACKS_ON_TIME, _REPLY_TIME, _PROPOSED, _SWITCHED_OFF, _BY_SQL],
    )

    await _create_table(client, pod_id, "callbacks", _CALLBACK_COLUMNS)

    def callback(customer: str, days_before: int, done: bool | None):
        return {
            "customer": customer,
            "due": _day(today, days_before),
            "done": done,
            "conversation": f"https://example.test/c/{customer.lower()}",
        }

    await _insert(
        client,
        pod_id,
        "callbacks",
        [
            callback("Ada", 27, True),  # w0: done
            callback("Ben", 26, True),  # w0: done
            # w1: nothing due
            callback("Cy", 10, True),  # w2: done
            callback("Di", 9, False),  # w2: missed
            callback("Ed", 8, None),  # w2: never marked, so missed
            callback("Fay", 3, True),  # w3: done
            callback("Gus", 1, False),  # w3: missed
            callback("Hal", 0, False),  # due today: in no week that has ended
            callback("Ivy", -3, False),  # not due yet
        ],
    )

    await _create_table(client, pod_id, "conversations", _CONVERSATION_COLUMNS)

    def conversation(customer: str, days_before: int, reply_minutes: int | None):
        return {
            "customer": customer,
            "link": f"https://example.test/inbox/{customer.lower()}",
            "started_at": _moment(today, days_before),
            "first_reply_at": None
            if reply_minutes is None
            else _moment(today, days_before, minutes=reply_minutes),
        }

    await _insert(
        client,
        pod_id,
        "conversations",
        [
            conversation("Jo", 10, 45),  # w2: 45 minutes
            conversation("Kim", 4, 90),  # w3: 90 minutes
            conversation("Lu", 3, 20),  # w3: 20 minutes
            conversation("Mo", 2, 10),  # w3: 10 minutes
            conversation("Noa", 5, None),  # w3: no reply yet, so no value
        ],
    )
    return pod_id, today


def _weeks(preview: dict, key: str) -> list[dict]:
    by_key = {measure["key"]: measure for measure in preview["measures"]}
    return by_key[key]["weeks"]


def _shown(preview: dict, key: str) -> list[str]:
    return [week["shown"] for week in _weeks(preview, key)]


# --- The preview ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_counts_four_weeks_of_what_is_on_and_proposed(
    authenticated_client, fixed_test_org
):
    pod_id, today = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={}
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    preview = response.json()
    assert preview["windows"] == [
        {"start": _day(today, 28), "end": _day(today, 21)},
        {"start": _day(today, 21), "end": _day(today, 14)},
        {"start": _day(today, 14), "end": _day(today, 7)},
        {"start": _day(today, 7), "end": _day(today, 0)},
    ]
    # Position order; the switched-off row is not previewed, the proposal is.
    assert [m["key"] for m in preview["measures"]] == [
        "callbacks_on_time",
        "reply_time",
        "callbacks_missed",
        "counted_by_sql",
    ]

    assert _shown(preview, "callbacks_on_time") == [
        "2 of 2",
        "nothing to count",
        "1 of 3",
        "1 of 2",
    ]
    assert [w["met"] for w in _weeks(preview, "callbacks_on_time")] == [
        True,
        None,
        False,
        False,
    ]

    reply = _weeks(preview, "reply_time")
    assert [w["shown"] for w in reply] == [
        "nothing to count",
        "nothing to count",
        "45 minutes",
        "20 minutes",
    ]
    assert [w["met"] for w in reply] == [None, None, False, True]
    # The median of the three replies, of three values: the unanswered one has
    # no value and is in neither.
    assert (reply[-1]["value"], reply[-1]["total"]) == (pytest.approx(20), 3)

    assert _shown(preview, "callbacks_missed") == ["none", "none", "2", "1"]
    assert _shown(preview, "counted_by_sql") == ["2", "none", "3", "2"]


@pytest.mark.asyncio
async def test_preview_by_key_counts_a_measure_that_is_off(
    authenticated_client, fixed_test_org
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview",
        json={"keys": ["switched_off"], "weeks": 2},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    preview = response.json()
    assert [m["key"] for m in preview["measures"]] == ["switched_off"]
    assert _shown(preview, "switched_off") == ["3 of 3", "2 of 2"]


@pytest.mark.asyncio
async def test_preview_of_a_draft_counts_it_and_saves_nothing(
    authenticated_client, fixed_test_org
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview",
        json={
            "measure": {
                "key": "callbacks_done",
                "measure": "Callbacks done",
                "shape": "count",
                "unit_table": "callbacks",
                "time_column": "due",
                "test": "coalesce(done, false)",
                "value_unit": "callbacks",
                "aim": "higher",
                "target": 1,
            }
        },
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert _shown(response.json(), "callbacks_done") == [
        "2 callbacks",
        "none",
        "1 callback",
        "1 callback",
    ]
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/scorecard/records", params={"limit": 100}
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert "callbacks_done" not in {row["key"] for row in listed.json()["items"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"time_column": "deadline"}, "not a column of `callbacks`"),
        ({"test": "done; DROP TABLE callbacks"}, "may not contain `;`"),
        ({"test": "id IN (SELECT id FROM conversations)"}, "may not use `select`"),
        ({"unit_table": "tickets"}, "no table by that name"),
    ],
)
async def test_preview_refuses_a_draft_that_cannot_run_and_runs_nothing(
    authenticated_client, fixed_test_org, changes, reason
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)
    draft = {
        "shape": "share",
        "unit_table": "callbacks",
        "time_column": "due",
        "test": "coalesce(done, false)",
        "aim": "higher",
        "target": 1,
    }
    draft.update(changes)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={"measure": draft}
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, response.text
    assert response.json()["code"] == "SCORECARD_REFUSED"
    assert reason in response.json()["message"]
    intact = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/query",
        json={"query": "SELECT count(*) AS n FROM callbacks"},
    )
    assert intact.status_code == status.HTTP_200_OK, intact.text
    assert intact.json()["items"][0]["n"] == 9


@pytest.mark.asyncio
async def test_preview_refuses_keys_and_a_draft_together(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview",
        json={"keys": ["a"], "measure": {"aim": "higher", "target": 1}},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_preview_of_a_pod_without_a_scorecard_is_not_found(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={}
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND, response.text
    assert response.json()["code"] == "SCORECARD_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_unit_table_that_is_gone_fails_its_measure_and_not_the_preview(
    authenticated_client, fixed_test_org
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)
    dropped = await authenticated_client.delete(
        f"/pods/{pod_id}/datastore/tables/conversations"
    )
    assert dropped.status_code == status.HTTP_204_NO_CONTENT, dropped.text

    response = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={"weeks": 1}
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    preview = response.json()
    (reply,) = _weeks(preview, "reply_time")
    assert reply["status"] == "failed"
    assert "no table by that name" in reply["reason"]
    assert _shown(preview, "callbacks_on_time") == ["1 of 2"]


# --- The rows behind a number ---------------------------------------------------------


@pytest.mark.asyncio
async def test_rows_behind_a_share_list_its_misses_first(
    authenticated_client, fixed_test_org
):
    pod_id, today = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.get(
        f"/pods/{pod_id}/scorecard/measures/callbacks_on_time/rows"
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body["start"], body["end"]) == (_day(today, 7), _day(today, 0))
    assert body["truncated"] is False
    assert [
        (row["label"], row["link"], row["at"], row["passed"]) for row in body["rows"]
    ] == [
        ("Gus", "https://example.test/c/gus", _day(today, 1), False),
        ("Fay", "https://example.test/c/fay", _day(today, 3), True),
    ]
    assert all(UUID(row["id"]) for row in body["rows"])


@pytest.mark.asyncio
async def test_rows_behind_a_number_are_cut_at_the_limit_and_say_so(
    authenticated_client, fixed_test_org
):
    pod_id, today = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.get(
        f"/pods/{pod_id}/scorecard/measures/callbacks_on_time/rows",
        params={"end": _day(today, 7), "limit": 2},
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert [row["label"] for row in body["rows"]] == ["Di", "Ed"]
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_rows_behind_a_median_mark_the_rows_it_was_taken_over(
    authenticated_client, fixed_test_org
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.get(
        f"/pods/{pod_id}/scorecard/measures/reply_time/rows"
    )

    assert response.status_code == status.HTTP_200_OK, response.text
    rows = response.json()["rows"]
    # Counted rows first; the conversation nobody has answered has no value.
    assert [(row["label"], row["passed"]) for row in rows] == [
        ("Kim", True),
        ("Lu", True),
        ("Mo", True),
        ("Noa", False),
    ]
    assert rows[0]["link"] == "https://example.test/inbox/kim"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "params", "code", "expected"),
    [
        ("counted_by_sql", {}, 422, "SCORECARD_MEASURE_HAS_NO_ROWS"),
        ("nope", {}, 404, "SCORECARD_MEASURE_NOT_FOUND"),
        ("callbacks_on_time", {"end": "2999-01-01"}, 422, "SCORECARD_REFUSED"),
    ],
)
async def test_rows_are_refused_for_what_has_none_to_show(
    authenticated_client, fixed_test_org, key, params, code, expected
):
    pod_id, _ = await _seed(authenticated_client, fixed_test_org)

    response = await authenticated_client.get(
        f"/pods/{pod_id}/scorecard/measures/{key}/rows", params=params
    )

    assert response.status_code == code, response.text
    assert response.json()["code"] == expected


# --- Counted as the caller ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_per_person_table_is_counted_as_each_person_sees_it(
    authenticated_client, async_client, fixed_test_org
):
    today = datetime.now(timezone.utc).date()
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_table(authenticated_client, pod_id, "scorecard", _SCORECARD_COLUMNS)
    await _insert(
        authenticated_client,
        pod_id,
        "scorecard",
        [
            _measure(
                "tasks_done",
                1,
                shape="count",
                unit_table="tasks",
                time_column="due",
                aim="higher",
                target=1,
            )
        ],
    )
    await _create_table(
        authenticated_client,
        pod_id,
        "tasks",
        [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "title", "type": "TEXT"},
            {"name": "due", "type": "DATE"},
        ],
        rls=True,
    )
    member = await signup_user(async_client, "scorecard-preview-member")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=member
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_USER",
    )
    as_member = {"headers": auth_headers(member)}
    await _insert(
        authenticated_client,
        pod_id,
        "tasks",
        [
            {"title": "owner one", "due": _day(today, 2)},
            {"title": "owner two", "due": _day(today, 3)},
        ],
    )
    await _insert(
        async_client,
        pod_id,
        "tasks",
        [{"title": "member one", "due": _day(today, 2)}],
        **as_member,
    )

    mine = await authenticated_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={"weeks": 1}
    )
    theirs = await async_client.post(
        f"/pods/{pod_id}/scorecard/preview", json={"weeks": 1}, **as_member
    )
    their_rows = await async_client.get(
        f"/pods/{pod_id}/scorecard/measures/tasks_done/rows", **as_member
    )

    assert mine.status_code == status.HTTP_200_OK, mine.text
    assert theirs.status_code == status.HTTP_200_OK, theirs.text
    assert _shown(mine.json(), "tasks_done") == ["2"]
    assert _shown(theirs.json(), "tasks_done") == ["1"]
    assert their_rows.status_code == status.HTTP_200_OK, their_rows.text
    assert [row["label"] for row in their_rows.json()["rows"]] == ["member one"]


# --- The teammate's dry run -------------------------------------------------------------


def _run_ctx(*, user_id: str, pod_id: str) -> SimpleNamespace:
    """The pod default agent's run context, as ``run_context_builder`` makes it."""
    pod = UUID(pod_id)
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(user_id),
            pod_id=pod,
            conversation_id=uuid4(),
            workload_type="agent",
            workload_id=pod,
            agent_name="pod_default",
            is_pod_default_agent=is_pod_default_agent(pod, pod_id=pod),
        )
    )


@pytest.mark.asyncio
async def test_try_measure_shows_a_draft_its_four_weeks_or_why_it_cannot_run(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id, today = await _seed(authenticated_client, fixed_test_org)
    ctx = _run_ctx(user_id=fixed_test_user["id"], pod_id=pod_id)
    draft = TryMeasureRequest(
        key="callbacks_on_time_draft",
        measure="Callbacks happen by their date",
        shape="share",
        unit_table="callbacks",
        time_column="due",
        test="coalesce(done, false)",
        aim="higher",
        target=1,
        target_label="every one",
    )

    tried = await try_measure(ctx, draft)

    assert tried["success"] is True, tried
    assert [week["shown"] for week in tried["weeks"]] == [
        "2 of 2",
        "nothing to count",
        "1 of 3",
        "1 of 2",
    ]
    assert tried["windows"][-1] == {"start": _day(today, 7), "end": _day(today, 0)}
    assert "proposed_from" in tried["note"]

    refused = await try_measure(
        ctx, draft.model_copy(update={"time_column": "deadline"})
    )

    assert refused["success"] is False
    assert "not a column of `callbacks`" in refused["error"]
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/scorecard/records", params={"limit": 100}
    )
    assert "callbacks_on_time_draft" not in {
        row["key"] for row in listed.json()["items"]
    }
