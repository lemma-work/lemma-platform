from __future__ import annotations

from http import HTTPStatus

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def test_manager_lifecycle_contract_is_minimal_and_real(agentbox_server, sandbox_id):
    manager = agentbox_server.client

    missing_auth = agentbox_server.anonymous_client.request_json(
        "GET",
        f"/sandboxes/{sandbox_id}",
    )
    assert missing_auth.status_code in {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }

    missing = manager.request_json("GET", f"/sandboxes/{sandbox_id}")
    assert missing.status_code == HTTPStatus.NOT_FOUND

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"AGENTBOX_E2E_VALUE": "lifecycle"}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text
    assert created.json() == {
        "sandbox": {
            "id": sandbox_id,
            "ready": True,
            "status": "RUNNING",
        }
    }

    fetched = manager.request_json("GET", f"/sandboxes/{sandbox_id}")
    assert fetched.status_code == HTTPStatus.OK, fetched.text
    assert fetched.json() == {
        "id": sandbox_id,
        "ready": True,
        "status": "RUNNING",
    }

    idempotent = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"AGENTBOX_E2E_VALUE": "ignored-after-create"}},
        timeout=180,
    )
    assert idempotent.status_code == HTTPStatus.OK, idempotent.text
    assert idempotent.json()["sandbox"] == fetched.json()

    no_old_contract_fields = set(fetched.json())
    assert no_old_contract_fields == {"id", "ready", "status"}
    assert "runtime_url" not in fetched.json()
    assert "apps" not in fetched.json()
    assert "image" not in fetched.json()
    assert "disk_size_gb" not in fetched.json()
    assert "namespace" not in fetched.json()
    assert "pod_name" not in fetched.json()

    deleted = manager.request_json("DELETE", f"/sandboxes/{sandbox_id}")
    assert deleted.status_code == HTTPStatus.OK, deleted.text
    assert deleted.json() == {"sandbox_id": sandbox_id, "deleted": True}

    after_delete = manager.request_json("GET", f"/sandboxes/{sandbox_id}")
    assert after_delete.status_code == HTTPStatus.NOT_FOUND


def test_manager_rejects_removed_create_fields(agentbox_server, sandbox_id):
    response = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={
            "env": {},
            "image": "should-not-be-accepted",
            "disk_size_gb": 50,
            "wait_ready": False,
        },
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
