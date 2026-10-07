"""Importing a template that ships with the backend, the way a hire does.

Real ASGI app and the real streaq worker: start a TEMPLATE import by name, let
the worker plan it from the archive the API staged, apply it with no variables
and no confirmation, and check the pod holds what the template carries.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import new_pod, wait_import

pytestmark = [pytest.mark.e2e, pytest.mark.worker]

_SUPPORT_DESK_SKILLS = ("answer-from-help-center", "bug-report", "hand-over")


async def test_support_desk_template_sets_up_a_fresh_pod(
    authenticated_client, fixed_test_org, worker
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Template Hire")

    started = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "TEMPLATE", "template": "support-desk"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    assert started.json()["source_kind"] == "TEMPLATE"
    import_id = started.json()["import_id"]

    planned = await wait_import(
        client, pod_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    plan = planned["plan"]
    assert plan["bundle_name"] == "Support desk"
    assert plan["description"]
    assert [v["name"] for v in plan["variables"] if v["required"]] == []
    assert plan["has_destructive_steps"] is False

    # Exactly what a hire sends: nothing to answer, nothing to confirm.
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"variables": {}, "confirm_destructive": False},
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final
    assert final["warnings"] == [], final["warnings"]

    for table in ("scorecard", "known_issues", "callbacks", "conversations"):
        got = await client.get(f"/pods/{pod_id}/datastore/tables/{table}")
        assert got.status_code == status.HTTP_200_OK, got.text

    records = await client.get(
        f"/pods/{pod_id}/datastore/tables/scorecard/records", params={"limit": 100}
    )
    assert records.status_code == status.HTTP_200_OK, records.text
    rows = {row["key"]: row for row in records.json()["items"]}
    assert set(rows) == {
        "known_issues_linked",
        "callbacks_on_time",
        "first_reply",
        "closed_without_handover",
        "reply_time",
        "standing_work",
        "open_questions",
    }
    # Seeded from CSV text: the cells must arrive typed, not as strings, and
    # an empty cell as nothing rather than as an empty test.
    assert all(row["is_on"] is True for row in rows.values())
    assert rows["first_reply"]["target"] == pytest.approx(0.9)
    assert rows["open_questions"]["target"] == pytest.approx(0.0)
    assert rows["standing_work"]["position"] == 6
    assert rows["reply_time"]["shape"] == "median"
    assert rows["reply_time"]["test"] is None

    # Every measure the template brings runs against the tables it brings:
    # empty, so nothing to count, but nothing refused either.
    preview = await client.post(f"/pods/{pod_id}/scorecard/preview", json={})
    assert preview.status_code == status.HTTP_200_OK, preview.text
    statuses = {
        measure["key"]: {week["status"] for week in measure["weeks"]}
        for measure in preview.json()["measures"]
    }
    assert set(statuses) == set(rows)
    assert all("failed" not in found for found in statuses.values()), statuses

    for skill in _SUPPORT_DESK_SKILLS:
        got = await client.get(
            f"/pods/{pod_id}/datastore/files/by-path",
            params={"path": f"/skills/{skill}/SKILL.md"},
        )
        assert got.status_code == status.HTTP_200_OK, got.text
        assert got.json()["visibility"] == "POD", got.json()

    pod = await client.get(f"/pods/{pod_id}")
    recipes = pod.json()["config"].get("recipes", [])
    assert any(
        recipe["kind"] == "TEMPLATE" and recipe["repo_url"] == "template:support-desk"
        for recipe in recipes
    ), recipes
    # The pod was made with no description, so the template's own is written.
    assert pod.json()["description"] == plan["description"]


async def test_a_template_keeps_a_description_the_pod_already_has(
    authenticated_client, fixed_test_org, worker
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Template Described")
    own = "Chases what the team promised, our way."
    described = await client.put(f"/pods/{pod_id}", json={"description": own})
    assert described.status_code == status.HTTP_200_OK, described.text

    started = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "TEMPLATE", "template": "follow-ups"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    import_id = started.json()["import_id"]
    planned = await wait_import(
        client, pod_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"variables": {}, "confirm_destructive": False},
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    pod = await client.get(f"/pods/{pod_id}")
    assert pod.json()["description"] == own

    # And its measures run against the commitments table it brings.
    preview = await client.post(f"/pods/{pod_id}/scorecard/preview", json={})
    assert preview.status_code == status.HTTP_200_OK, preview.text
    failed = [
        (measure["key"], week["reason"])
        for measure in preview.json()["measures"]
        for week in measure["weeks"]
        if week["status"] == "failed"
    ]
    assert failed == []
    assert [m["key"] for m in preview.json()["measures"]] == [
        "followed_up",
        "nothing_overdue",
        "closed_on_time",
        "standing_work",
        "open_questions",
    ]


async def test_an_unknown_template_is_not_found(
    authenticated_client, fixed_test_org, worker
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Template Missing")

    res = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "TEMPLATE", "template": "no-such-role"},
    )
    assert res.status_code == status.HTTP_404_NOT_FOUND, res.text
    assert res.json()["code"] == "POD_BUNDLE_TEMPLATE_NOT_FOUND"


async def test_a_template_name_with_a_path_in_it_is_refused(
    authenticated_client, fixed_test_org, worker
):
    client = authenticated_client
    pod_id = await new_pod(client, fixed_test_org["id"], label="Template Traversal")

    res = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "TEMPLATE", "template": "../support-desk"},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, res.text


async def test_the_shelf_lists_every_template_with_its_card(authenticated_client):
    """Not pod-scoped and beside ``/pods/{pod_id}/…``: the path must reach the
    catalog rather than be read as a pod id."""
    listed = await authenticated_client.get("/pods/bundle/templates")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    cards = {card["template"]: card for card in listed.json()["items"]}

    assert {"support-desk", "follow-ups"} <= set(cards)
    support = cards["support-desk"]
    assert support["name"] == "Support desk"
    assert support["judged_on"][0] == "Known issues have a tracker issue"
    assert "Standing work runs on time" not in support["judged_on"]
    assert {skill["name"] for skill in support["skills"]} == set(_SUPPORT_DESK_SKILLS)
    assert "scorecard" not in {table["name"] for table in support["tables"]}
    assert any(win.get("needs") for win in support["wins"])
