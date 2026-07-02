"""End-to-end pod bundle import from GitHub.

The worker's zipball fetch is redirected (POD_BUNDLE_GITHUB_API_BASE, set in the
conftest before the worker spawns) to a local threaded HTTP server that serves a
``pack_bundle`` archive for any ``/repos/*/zipball*`` path. So the real
``import_pod_github`` job runs — fetch → stage → plan — without touching the
network.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from lemma_pod_bundle import pack_bundle

from .conftest import GITHUB_FIXTURE_PORT

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _fixture_zip() -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "bundle"
        (root).mkdir(parents=True)
        (root / "pod.json").write_text(
            json.dumps({"name": "GitHub CRM", "format_version": 2, "variables": {}}),
            encoding="utf-8",
        )
        tdir = root / "tables" / "leads"
        tdir.mkdir(parents=True)
        (tdir / "leads.json").write_text(
            json.dumps(
                {
                    "name": "leads",
                    "primary_key_column": "id",
                    "columns": [{"name": "id", "type": "UUID", "required": True}],
                }
            ),
            encoding="utf-8",
        )
        adir = root / "agents" / "greeter"
        adir.mkdir(parents=True)
        (adir / "greeter.json").write_text(
            json.dumps({"name": "greeter", "instruction": "Hi."}), encoding="utf-8"
        )
        return pack_bundle(root)


@pytest.fixture(scope="session")
def github_fixture_server():
    """A tiny server that returns a fixed bundle zip for any zipball request."""
    zip_bytes = _fixture_zip()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if "/zipball" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(zip_bytes)))
                self.end_headers()
                self.wfile.write(zip_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # silence
            return

    server = ThreadingHTTPServer(("127.0.0.1", GITHUB_FIXTURE_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()


async def _wait(client, pod_id, import_id, *, until, timeout=60) -> dict:
    for _ in range(timeout):
        res = await client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        body = res.json()
        if body["status"] in until:
            return body
        await asyncio.sleep(1)
    raise AssertionError(f"Import stuck at {body['status']}")


async def test_github_import_plans_from_repo(
    authenticated_client, test_pod, worker, github_fixture_server
):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/github",
        json={"repo_url": f"https://github.com/acme/crm-{uuid4().hex[:6]}"},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    body = res.json()
    assert body["source_kind"] == "github"
    import_id = body["import_id"]

    final = await _wait(
        authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION", "FAILED"}
    )
    assert final["status"] == "AWAITING_CONFIRMATION", final
    steps = {(s["kind"], s["name"]) for s in final["plan"]["steps"]}
    assert ("TABLE", "leads") in steps
    assert ("AGENT", "greeter") in steps

    # And it applies like any other import.
    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    applied = await _wait(
        authenticated_client, pod_id, import_id, until={"COMPLETED", "FAILED"}
    )
    assert applied["status"] == "COMPLETED", applied
    tbl = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads")
    assert tbl.status_code == status.HTTP_200_OK


async def test_github_import_rejects_bad_repo(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/github",
        json={"repo_url": "definitely not a repo!!!"},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, res.text
    assert res.json()["code"] == "POD_BUNDLE_INVALID"
