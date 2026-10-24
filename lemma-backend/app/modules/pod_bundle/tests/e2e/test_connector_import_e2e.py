"""Use-case e2e: bundled connector-bound resources name their pod connector.

A surface or schedule carries the pod connector it runs through by slug, and
the bundle declares that connector in ``connectors/<slug>/`` as its catalog
connector and mode -- so a plan asks for no account variable at all; it asks
which of the importer's installations each connector maps to. A bundle that
still asks for an account predates that and is refused at planning, naming
the variable.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    pack_fixture_bundle,
    start_and_plan_import,
    wait_import,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def test_a_bundled_surface_names_its_pod_connector_and_asks_for_no_account(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connectors")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]
    assert plan["variables"] == [], plan["variables"]
    assert any(s["kind"] == "SURFACE" for s in plan["steps"]), plan["steps"]
    [connector] = plan["connectors"]
    assert (connector["slug"], connector["connector"], connector["mode"]) == (
        "slack-shared",
        "slack",
        "SHARED",
    )
    assert connector["used_by"] == ["surfaces/slack"]


async def test_a_bundled_schedule_names_its_pod_connector_and_asks_for_no_account(
    authenticated_client, test_pod, worker
):
    """A schedule carries its pod connector by slug, and the bundle declares
    that connector: the plan asks for no account at all. Applying it into an
    organization with no installation of the connector is refused before
    anything is written, naming the connector -- never a schedule created
    listening through nothing."""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connector_schedule")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]
    assert not [v for v in plan["variables"] if v["kind"] == "account"], plan
    assert any(s["kind"] == "SCHEDULE" for s in plan["steps"]), plan["steps"]

    [connector] = plan["connectors"]
    assert (connector["slug"], connector["mode"]) == ("jira", "PER_MEMBER")
    assert connector["status"] == "NEEDS_INSTALLATION"
    assert plan["steps"][0]["kind"] == "CONNECTOR"

    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == 422, apply.text
    assert apply.json()["details"]["blocking"][0]["slug"] == "jira", apply.text
    schedules = await authenticated_client.get(f"/pods/{pod_id}/schedules")
    assert "on_ticket" not in schedules.text


async def test_a_bundle_asking_for_an_account_still_plans(
    authenticated_client, test_pod, worker
):
    """Exported before pod connectors: deprecated, and still imported. The
    account variable is offered as optional, so a template nobody re-exported
    plans instead of failing."""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("asks_for_an_account")

    up = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports", json={"kind": "URL", "url": up.json()["url"]}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]

    body = await wait_import(
        authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION"}
    )
    [variable] = body["plan"]["variables"]
    assert variable["name"] == "on_ticket_account"
    assert (variable["required"], variable["default"]) == (False, None)
    assert "Deprecated" in variable["description"]
