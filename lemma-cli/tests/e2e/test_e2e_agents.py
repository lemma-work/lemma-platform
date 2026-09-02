from __future__ import annotations

import json

import pytest

from .helpers import cli, cli_json, items_of

pytestmark = pytest.mark.e2e


@pytest.fixture
def pod_id(test_pod):
    return test_pod["id"]


def test_agents_create_and_list(backend_server, test_user, pod_id):
    agent_payload = json.dumps(
        {
            "name": "e2e-agent",
            "instruction": "You are a helpful test agent.",
        }
    )
    create = cli(
        ["agents", "create", "--data", agent_payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert create.exit_code == 0, create.stdout

    payload = cli_json(
        ["agents", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    names = [item.get("name") for item in items_of(payload)]
    assert "e2e-agent" in names


def test_agents_get(backend_server, test_user, pod_id):
    agent_payload = json.dumps(
        {
            "name": "e2e-get-agent",
            "instruction": "Test agent for get.",
        }
    )
    cli(
        ["agents", "create", "--data", agent_payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    result = cli(
        ["agents", "get", "e2e-get-agent"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert result.exit_code == 0, result.output
    assert "e2e-get-agent" in result.stdout


def test_agents_update(backend_server, test_user, pod_id):
    cli(
        [
            "agents",
            "create",
            "--data",
            json.dumps({"name": "e2e-update-agent", "instruction": "Original."}),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    update = cli(
        [
            "agents",
            "update",
            "e2e-update-agent",
            "--data",
            json.dumps({"instruction": "Updated instruction."}),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert update.exit_code == 0, update.stdout


def test_agents_delete(backend_server, test_user, pod_id):
    cli(
        [
            "agents",
            "create",
            "--data",
            json.dumps({"name": "e2e-delete-agent", "instruction": "To be deleted."}),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    delete = cli(
        ["agents", "delete", "e2e-delete-agent", "--yes"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert delete.exit_code == 0, delete.stdout

    payload = cli_json(
        ["agents", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    names = [item.get("name") for item in items_of(payload)]
    assert "e2e-delete-agent" not in names


def test_agents_delete_requires_yes(backend_server, test_user, pod_id):
    """Deleting without --yes in non-interactive mode should fail."""
    cli(
        [
            "agents",
            "create",
            "--data",
            json.dumps(
                {"name": "e2e-nodelete-agent", "instruction": "Should not be deleted."}
            ),
        ],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    result = cli(
        ["agents", "delete", "e2e-nodelete-agent"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert result.exit_code != 0
