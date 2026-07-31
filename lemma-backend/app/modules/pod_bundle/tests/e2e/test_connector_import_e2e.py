"""Use-case e2e: a bundled connector (surface) is planned as an importable step
whose account is a REQUIRED variable, and apply enforces it.

The exporter tokenizes a surface's ``account_id`` into a ``${..._account}``
variable because the source org's account id is meaningless in the target org.
This test proves the plan surfaces that variable as required and that applying
without it is rejected — so a connector is never silently imported unbound. (The
actual surface create + account binding is covered by the applier unit test, which
does not need a live connector account.)
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


async def test_connector_account_is_required_and_enforced(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connectors")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]

    # The connector account is surfaced as a REQUIRED variable...
    account_var = next(
        (v for v in plan["variables"] if v["name"] == "slack_account"), None
    )
    assert account_var is not None, plan["variables"]
    assert account_var["kind"] == "account"
    assert account_var["required"] is True
    # The connector is sent so the UI can prompt for the right one.
    assert account_var["connector"] == "slack"
    # ...and the auth provider, so the UI connects/creates through the right one.
    assert account_var["provider"] == "COMPOSIO"

    # ...and there is a SURFACE step to apply.
    assert any(s["kind"] == "SURFACE" for s in plan["steps"]), plan["steps"]

    # Applying without the required account is rejected before anything runs.
    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == 422, apply.text
    assert "slack_account" in apply.text


async def test_schedule_connector_account_is_required_and_enforced(
    authenticated_client, test_pod, worker
):
    """A schedule's account_id is tokenized exactly like a surface's: the plan
    must surface it as a required variable carrying connector + provider, and
    apply without it must be rejected. (Regression coverage for a bug where
    schedule account variables shipped with no connector info at all, and a
    separate bug where the applier silently dropped account_id even when
    resolved — both covered at the unit level in test_applier.py; this proves
    the plan/apply-gate contract end to end.)"""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connector_schedule")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]

    account_var = next(
        (v for v in plan["variables"] if v["name"] == "on_ticket_account"), None
    )
    assert account_var is not None, plan["variables"]
    assert account_var["kind"] == "account"
    assert account_var["required"] is True
    assert account_var["connector"] == "jira"
    assert account_var["provider"] == "COMPOSIO"

    assert any(s["kind"] == "SCHEDULE" for s in plan["steps"]), plan["steps"]

    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == 422, apply.text
    assert "on_ticket_account" in apply.text


async def test_legacy_bundle_missing_provider_fails_planning(
    authenticated_client, test_pod, worker
):
    """A bundle built before connector/provider was mandatory on account
    variables (or a hand-edited one) must fail plan-build with a clear
    error instead of importing with a variable the UI/CLI can't resolve to the
    right connector — the hard-reject decision for pre-existing bundles."""
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("legacy_schedule_missing_provider")

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

    body = await wait_import(authenticated_client, pod_id, import_id, until={"FAILED"})
    assert body["status"] == "FAILED"
    assert "on_ticket_account" in (body.get("error") or "")
