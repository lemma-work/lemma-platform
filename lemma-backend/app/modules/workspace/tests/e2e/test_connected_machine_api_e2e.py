"""A connected machine through the ``/machines`` API, as everyone else reaches it.

The person approves the machine; it is then ``mine:<name>`` in the machine
listing, and the same routes that run commands and move files on a cloud
machine do it on theirs -- the sandbox service routes it to the connected
fabric by its id, and the bytes go down the machine's link. Offline, the API
says so in a sentence and runs nothing anywhere else.
"""

from __future__ import annotations

import pytest

from app.modules.test_support.e2e.waiters import eventually
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.tests.e2e.connected_machine_harness import (
    running_machine,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def machines_on(monkeypatch):
    monkeypatch.setattr(workspace_settings, "machine_connections_enabled", True)
    monkeypatch.setattr(
        workspace_settings,
        "runtime_credential_key",
        "test-runtime-credential-key-32-bytes",
    )
    monkeypatch.setenv(
        "WORKSPACE_RUNTIME_CREDENTIAL_KEY", "test-runtime-credential-key-32-bytes"
    )


async def _output(client, process_id: str) -> str:
    response = await client.get(
        f"/machines/mine:laptop/processes/{process_id}/output",
        params={"after": 0, "wait_seconds": 1},
    )
    assert response.status_code == 200, response.text
    return "".join(chunk["data"] for chunk in response.json()["chunks"])


@pytest.mark.asyncio
async def test_a_connected_machine_is_listed_and_runs_through_the_machines_api(
    backend_server, async_client, authenticated_client, tmp_path
):
    async with running_machine(
        backend_server, async_client, authenticated_client, tmp_path / "root"
    ) as machine:
        listed = await authenticated_client.get("/machines")
        assert listed.status_code == 200, listed.text
        mine = [item for item in listed.json()["machines"] if item["name"] == "laptop"]
        assert mine and mine[0]["kind"] == "connected", listed.json()

        written = await authenticated_client.put(
            "/machines/mine:laptop/files/content",
            params={"path": "/home/user/lemma/api.txt"},
            content=b"through the api\n",
        )
        assert written.status_code == 200, written.text
        assert (machine.root / "lemma" / "api.txt").read_text() == "through the api\n"

        files = await authenticated_client.get(
            "/machines/mine:laptop/files",
            params={"path": "/home/user/lemma", "wake": True},
        )
        assert files.status_code == 200, files.text
        names = {entry["name"] for entry in files.json()["entries"]}
        assert "api.txt" in names

        started = await authenticated_client.post(
            "/machines/mine:laptop/processes",
            json={"command": "cat api.txt"},
        )
        assert started.status_code == 201, started.text
        process_id = started.json()["id"]
        said = await eventually(
            label="the process's output comes back over the link",
            probe=lambda: _output(authenticated_client, process_id),
            done=lambda text: "through the api" in text,
        )
        assert "through the api" in said

        await machine.stop()
        await eventually(
            label="the machine is offline",
            probe=lambda: authenticated_client.get("/machine-connections"),
            done=lambda response: (
                not any(item["online"] for item in response.json()["items"])
            ),
        )
        refused = await authenticated_client.post(
            "/machines/mine:laptop/processes", json={"command": "true"}
        )
        assert refused.status_code == 409, refused.text
        assert "laptop is not connected" in refused.text
