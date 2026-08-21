from __future__ import annotations

import json

import pytest

from .helpers import cli, cli_json, items_of

pytestmark = pytest.mark.e2e


@pytest.fixture
def pod_id(test_pod):
    return test_pod["id"]


def test_tables_create_and_list(backend_server, test_user, pod_id):
    table_payload = json.dumps(
        {
            "name": "items",
            "columns": [
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "score", "type": "INTEGER"},
            ],
            "enable_rls": False,
        }
    )
    create = cli(
        ["tables", "create", "items", "--data", table_payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert create.exit_code == 0, create.stdout

    payload = cli_json(
        ["tables", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    names = [item.get("name") for item in items_of(payload)]
    assert "items" in names


def test_tables_get(backend_server, test_user, pod_id):
    table_payload = json.dumps(
        {
            "name": "products",
            "columns": [{"name": "sku", "type": "TEXT", "required": True}],
            "enable_rls": False,
        }
    )
    cli(
        ["tables", "create", "products", "--data", table_payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    result = cli(
        ["tables", "get", "products"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert result.exit_code == 0, result.stdout
    assert "products" in result.stdout


def test_records_create_and_list(backend_server, test_user, pod_id):
    table_payload = json.dumps(
        {
            "name": "tasks",
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
            "enable_rls": False,
        }
    )
    cli(
        ["tables", "create", "tasks", "--data", table_payload],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )

    create_rec = cli(
        ["records", "create", "tasks", "--data", json.dumps({"title": "My Task"})],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    assert create_rec.exit_code == 0, create_rec.stdout

    payload = cli_json(
        ["records", "list", "tasks"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        pod=pod_id,
    )
    titles = [item.get("title") for item in items_of(payload)]
    assert "My Task" in titles
