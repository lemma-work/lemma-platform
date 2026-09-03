from __future__ import annotations

import pytest

from .helpers import cli, cli_json, items_of

pytestmark = pytest.mark.e2e


def test_orgs_list_shows_created_org(backend_server, test_user, test_org):
    """Creating an org via HTTP and listing via CLI returns that org."""
    payload = cli_json(
        ["orgs", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    org_ids = [item.get("id") for item in items_of(payload)]
    assert test_org["id"] in org_ids


def test_pods_create_and_list(backend_server, test_user, test_org):
    """Creating a pod via CLI and listing it returns it."""
    org_id = test_org["id"]
    pod_name = "e2e-test-pod"

    create_result = cli(
        ["pods", "create", pod_name, "--org", org_id],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    assert create_result.exit_code == 0, create_result.output

    payload = cli_json(
        ["pods", "list", "--org", org_id],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    names = [item.get("name") for item in items_of(payload)]
    assert pod_name in names


def test_pods_get_by_name(backend_server, test_user, test_pod, test_org):
    """Getting a pod by name resolves correctly (org scopes the name lookup)."""
    result = cli(
        ["pods", "get", test_pod["name"]],
        base_url=backend_server["base_url"],
        token=test_user["token"],
        org=test_org["id"],
    )
    assert result.exit_code == 0, result.output
    assert test_pod["name"] in result.stdout


def test_pods_get_by_uuid(backend_server, test_user, test_pod):
    """Getting a pod by UUID returns pod details."""
    result = cli(
        ["pods", "get", test_pod["id"]],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    assert result.exit_code == 0, result.output
    assert test_pod["name"] in result.stdout
