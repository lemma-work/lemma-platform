"""A grant on a connector the import left out waits for it, and lands.

The bundle's agent is granted ``connector.use`` on ``calendar``, which the
bundle marks optional. The importing organization has never installed that
catalog connector, so the import goes ahead without it. It used to drop the
grant, so when somebody installed the connector and the pod declared it, the
agent was refused it until the bundle was imported again. The grant now waits,
and is in place on whatever the pod's connector is called once it has one.

The worker, the database, the import job and the connectors module are real;
the catalog entry and the installation are rows, as the catalog import and the
Connectors page would have left them.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status
from lemma_pod_bundle import pack_bundle

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    start_and_plan_import,
    wait_import,
)
from app.modules.pod_bundle.tests.e2e.test_connector_bundle_e2e import (
    _catalog_connector,
    _install,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, connector_id: str) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": "Meetings", "format_version": 2})
    _write(
        root / "connectors" / "calendar" / "calendar.json",
        {"connector": connector_id, "mode": "PER_MEMBER", "required": False},
    )
    _write(
        root / "agents" / "briefer" / "briefer.json",
        {
            "name": "briefer",
            "instruction": "Brief me on my next meeting.",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "calendar",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )
    return pack_bundle(root)


async def _connector_grants(client, pod_id: str) -> list[dict]:
    got = await client.get(f"/pods/{pod_id}/agents/briefer/permissions")
    assert got.status_code == status.HTTP_200_OK, got.text
    return [g for g in got.json()["grants"] if g["resource_type"] == "connector"]


async def test_a_left_out_connectors_grant_lands_when_the_pod_declares_it(
    scenario, db_session, worker, tmp_path
):
    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Waiting")
    client, pod_id = scenario.owner_client, scenario.pod_id

    import_id = await start_and_plan_import(
        client, pod_id, _bundle(tmp_path, connector_id)
    )
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})

    assert final["status"] == "COMPLETED", final
    assert any("'calendar' waits for it" in w for w in final["warnings"]), final
    assert await _connector_grants(client, pod_id) == []

    # Somebody installs it and the pod declares it -- under a slug of its
    # own, not the bundle's -- with nothing imported again.
    install_id = await _install(db_session, scenario.org_id, connector_id)
    declared = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "my-cal", "mode": "PER_MEMBER"},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text

    grants = await _connector_grants(client, pod_id)
    assert [(g["resource_name"], g["permission_ids"]) for g in grants] == [
        ("my-cal", ["connector.use"])
    ]

    # Granted once: a second connector of the same kind gets nothing.
    second = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "cal-2", "mode": "PER_MEMBER"},
    )
    assert second.status_code == status.HTTP_201_CREATED, second.text
    assert [g["resource_name"] for g in await _connector_grants(client, pod_id)] == [
        "my-cal"
    ]


async def test_a_left_out_connector_the_org_has_installed_still_drops_its_grant(
    scenario, db_session, worker, tmp_path
):
    """Leaving out a connector the org has an installation of is the
    importer's choice, and its grant is dropped as they were told."""
    connector_id = await _catalog_connector(db_session)
    await scenario.create_org_with_pod(name_prefix="Chose")
    client, pod_id = scenario.owner_client, scenario.pod_id
    install_id = await _install(db_session, scenario.org_id, connector_id)

    import_id = await start_and_plan_import(
        client, pod_id, _bundle(tmp_path, connector_id)
    )
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"connectors": {"calendar": {"skip": True}}},
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final
    assert any("'calendar' was dropped" in w for w in final["warnings"]), final

    declared = await client.post(
        f"/pods/{pod_id}/connectors",
        json={"auth_config_id": install_id, "slug": "my-cal", "mode": "PER_MEMBER"},
    )
    assert declared.status_code == status.HTTP_201_CREATED, declared.text
    assert await _connector_grants(client, pod_id) == []
