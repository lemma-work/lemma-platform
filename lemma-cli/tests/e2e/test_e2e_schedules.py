from __future__ import annotations

import json

import pytest

from .helpers import cli, cli_json, items_of

pytestmark = pytest.mark.e2e


@pytest.fixture
def pod_id(test_pod):
    return test_pod["id"]


@pytest.fixture
def test_agent(backend_server, test_user, pod_id):
    payload = json.dumps({"name": "sched-agent", "instruction": "Test."})
    cli(
        ["agents", "create", "--data", payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    yield "sched-agent"


def test_schedule_create_cron_and_list(backend_server, test_user, pod_id, test_agent):
    create = cli(
        [
            "schedules",
            "create",
            "--name",
            "daily-cron",
            "--agent",
            test_agent,
            "--cron",
            "0 9 * * 1",
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert create.exit_code == 0, create.stdout

    payload = cli_json(
        ["schedules", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    names = [item.get("name") for item in items_of(payload)]
    assert "daily-cron" in names


def test_schedule_delete(backend_server, test_user, pod_id, test_agent):
    cli(
        [
            "schedules",
            "create",
            "--name",
            "to-delete-sched",
            "--agent",
            test_agent,
            "--cron",
            "0 8 * * *",
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    delete = cli(
        ["schedules", "delete", "to-delete-sched", "--yes"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert delete.exit_code == 0, delete.stdout

    payload = cli_json(
        ["schedules", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    names = [item.get("name") for item in items_of(payload)]
    assert "to-delete-sched" not in names
