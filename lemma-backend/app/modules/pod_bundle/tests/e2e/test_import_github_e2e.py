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

from app.modules.pod_bundle.infrastructure.github_publisher import (
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.state import PublishMode
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


def _pack_published_files(files: dict[str, bytes]) -> bytes:
    """Materialize an in-memory published repository as a GitHub zipball."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "published-repo"
        root.mkdir(parents=True)
        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return pack_bundle(root)


def _serve_archive(state: dict, content: bytes) -> None:
    state.update(
        zip_bytes=content,
        status=200,
        requests=0,
        declared_length=None,
    )


class _MemoryGithubOps:
    """The publisher's GitHub boundary, backed by an in-memory repository."""

    def __init__(self):
        self.repo: RepoCreateResult | None = None
        self.files: dict[str, bytes] = {}
        self.head = "head-0"

    async def resolve_repo(self, *, name):
        return self.repo

    async def create_repo(self, *, name, private, description):
        self.repo = RepoCreateResult(
            owner="acme", repo=name, html_url=f"https://github.com/acme/{name}"
        )
        self.files["README.md"] = b"# Initialized"
        return self.repo

    async def get_head(self, *, owner, repo, branch):
        return self.head

    async def get_file(self, *, owner, repo, path, ref=None):
        return self.files.get(path)

    async def commit_files(
        self,
        *,
        owner,
        repo,
        branch,
        upserts,
        deletes,
        message,
        expected_head,
    ):
        assert expected_head == self.head
        self.files.update(upserts)
        for path in deletes:
            self.files.pop(path, None)
        self.head = f"head-{uuid4().hex}"
        return self.head


@pytest.fixture(scope="session")
def github_fixture_server():
    """A tiny server whose GitHub zipball response tests can replace."""
    state = {
        "zip_bytes": _fixture_zip(),
        "status": 200,
        "requests": 0,
        "declared_length": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if "/zipball" in self.path:
                state["requests"] += 1
                response_status = int(state["status"])
                self.send_response(response_status)
                if response_status == 429:
                    self.send_header("X-RateLimit-Remaining", "0")
                if response_status != 200:
                    self.end_headers()
                    return
                zip_bytes = state["zip_bytes"]
                self.send_header("Content-Type", "application/zip")
                declared_length = state["declared_length"]
                self.send_header(
                    "Content-Length",
                    str(
                        declared_length
                        if declared_length is not None
                        else len(zip_bytes)
                    ),
                )
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
        yield state
    finally:
        server.shutdown()
        server.server_close()


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
    _serve_archive(github_fixture_server, _fixture_zip())
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": f"https://github.com/acme/crm-{uuid4().hex[:6]}"},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    body = res.json()
    assert body["source_kind"] == "GITHUB"
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


async def test_published_repository_roundtrips_through_github_import(
    authenticated_client, test_pod, worker, github_fixture_server
):
    """Publish layout -> GitHub zipball -> import plan -> apply in a real worker."""
    repo_name = f"published-{uuid4().hex[:6]}"
    first_large_file = bytes(range(251)) * 1_600
    large_file = first_large_file[::-1]
    table_definition = {
        "name": "published_accounts",
        "primary_key_column": "id",
        "columns": [{"name": "id", "type": "UUID", "required": True}],
    }
    ops = _MemoryGithubOps()
    await GithubPublisher(ops).publish(
        publish_id=str(uuid4()),
        mode=PublishMode.CREATE,
        repo_name=repo_name,
        private=False,
        description="Published E2E fixture",
        files={
            "pod.json": json.dumps(
                {"name": "Published Pod", "format_version": 2, "variables": {}}
            ).encode(),
            "tables/published_accounts/published_accounts.json": json.dumps(
                table_definition
            ).encode(),
            # Exercise manifest-backed chunk reassembly inside both real workers,
            # then verify the applied datastore file byte-for-byte.
            "files/large-fixture.bin": first_large_file,
            "files/stale-after-update.txt": b"remove me",
        },
        readme="# Published Pod\n",
    )
    assert any(".chunk" in path for path in ops.files)
    ops.files["notes/keep.md"] = b"unrelated repository content"

    # Update the same repository before a fresh import. Managed stale paths are
    # removed, unrelated repository content survives, and the new large bytes
    # are what the real worker imports.
    await GithubPublisher(ops).publish(
        publish_id=str(uuid4()),
        mode=PublishMode.UPDATE,
        repo_name=repo_name,
        private=False,
        description="Updated E2E fixture",
        files={
            "pod.json": json.dumps(
                {"name": "Published Pod", "format_version": 2, "variables": {}}
            ).encode(),
            "tables/published_accounts/published_accounts.json": json.dumps(
                table_definition
            ).encode(),
            "files/large-fixture.bin": large_file,
        },
        readme="# Published Pod\n",
        already_created=ops.repo,
    )
    assert ops.files["notes/keep.md"] == b"unrelated repository content"
    assert "files/stale-after-update.txt" not in ops.files
    _serve_archive(github_fixture_server, _pack_published_files(ops.files))

    pod_id = test_pod["id"]
    started = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": f"https://github.com/acme/{repo_name}"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    import_id = started.json()["import_id"]

    planned = await _wait(
        authenticated_client,
        pod_id,
        import_id,
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )
    assert planned["status"] == "AWAITING_CONFIRMATION", planned
    assert ("TABLE", "published_accounts") in {
        (step["kind"], step["name"]) for step in planned["plan"]["steps"]
    }

    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    applied = await _wait(
        authenticated_client, pod_id, import_id, until={"COMPLETED", "FAILED"}
    )
    assert applied["status"] == "COMPLETED", applied
    table = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/published_accounts"
    )
    assert table.status_code == status.HTTP_200_OK
    downloaded = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files/download",
        params={"path": "/large-fixture.bin"},
    )
    assert downloaded.status_code == status.HTTP_200_OK, downloaded.text
    assert downloaded.content == large_file


async def test_github_import_rejects_incomplete_published_chunks(
    authenticated_client, test_pod, worker, github_fixture_server
):
    ops = _MemoryGithubOps()
    await GithubPublisher(ops).publish(
        publish_id=str(uuid4()),
        mode=PublishMode.CREATE,
        repo_name=f"incomplete-{uuid4().hex[:6]}",
        private=False,
        description=None,
        files={
            "pod.json": json.dumps(
                {"name": "Incomplete", "format_version": 2, "variables": {}}
            ).encode(),
            "payloads/large.bin": b"x" * 400_000,
        },
        readme="# Incomplete\n",
    )
    missing = next(path for path in ops.files if ".chunk" in path)
    del ops.files[missing]
    _serve_archive(github_fixture_server, _pack_published_files(ops.files))

    pod_id = test_pod["id"]
    started = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": "https://github.com/acme/incomplete"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    final = await _wait(
        authenticated_client,
        pod_id,
        started.json()["import_id"],
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )
    assert final["status"] == "FAILED", final
    assert final["error_code"] == "POD_BUNDLE_INVALID"
    assert "missing" in final["error"].lower()


async def test_github_import_retries_rate_limit_then_fails(
    authenticated_client, test_pod, worker, github_fixture_server
):
    _serve_archive(github_fixture_server, _fixture_zip())
    github_fixture_server["status"] = 429
    pod_id = test_pod["id"]
    started = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": "https://github.com/acme/rate-limited"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    final = await _wait(
        authenticated_client,
        pod_id,
        started.json()["import_id"],
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )
    assert final["status"] == "FAILED", final
    assert final["error_code"] == "GITHUB_RATE_LIMITED"
    assert final["retryable"] is False
    assert github_fixture_server["requests"] == 3


async def test_github_import_rejects_oversized_declared_archive(
    authenticated_client, test_pod, worker, github_fixture_server
):
    _serve_archive(github_fixture_server, _fixture_zip())
    github_fixture_server["declared_length"] = (
        pod_bundle_settings.pod_bundle_max_archive_bytes + 1
    )
    pod_id = test_pod["id"]
    started = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": "https://github.com/acme/too-large"},
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text
    final = await _wait(
        authenticated_client,
        pod_id,
        started.json()["import_id"],
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )
    assert final["status"] == "FAILED", final
    assert final["error_code"] == "GITHUB_ARCHIVE_TOO_LARGE"
    assert final["retryable"] is False
    assert github_fixture_server["requests"] == 1


async def test_github_import_rejects_bad_repo(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "GITHUB", "url": "definitely not a repo!!!"},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, res.text
    assert res.json()["code"] == "POD_BUNDLE_INVALID"
