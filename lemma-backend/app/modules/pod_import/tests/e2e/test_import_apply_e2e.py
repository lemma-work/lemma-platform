"""End-to-end: plan and apply a real bundle through the pod_import endpoints,
against the full stack (Postgres/Redis), and confirm the resources land.

Exercises the whole engine: controller -> ImportAppService -> plan builder ->
PodImportEntity -> ImportService loop -> BackendResourceApplier -> the real
TableService / AgentService -> DB. Also validates the 0002_pod_imports
migration (the test DB is migrated on setup).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

pytestmark = pytest.mark.e2e


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _dist_zip() -> bytes:
    """A minimal valid app dist archive (must contain index.html)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", "<!doctype html><title>mini app</title>")
    return buffer.getvalue()


def _zip_bundle(bundle_root: Path) -> bytes:
    """Zip the bundle dir as a client would before upload."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_root.parent))
    return buffer.getvalue()


def _wrap_in_folder(archive: bytes, folder: str) -> bytes:
    """Re-zip every entry under an extra top-level folder — simulates a GitHub
    codeload zipball's own "<repo>-<ref>/" wrapper stacking on top of whatever
    the archive already contains."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        with zipfile.ZipFile(io.BytesIO(archive)) as src:
            for info in src.infolist():
                if info.is_dir():
                    continue
                zf.writestr(f"{folder}/{info.filename}", src.read(info))
    return buffer.getvalue()


def _stage_bundle(root: Path) -> Path:
    """A bundle covering the wired handlers: a table with seed data, an
    inline-instruction agent, and a (code-less) function."""
    _write(root / "pod.json", {"name": "import-e2e", "format_version": 2})
    # No top-level "name" — it comes from the directory (real bundles omit it).
    _write(root / "tables" / "widgets" / "widgets.json", {
        "primary_key_column": "id",
        "enable_rls": False,
        "columns": [
            {"name": "id", "type": "UUID", "auto": True, "required": True, "unique": True},
            {"name": "label", "type": "TEXT", "required": True},
            {"name": "qty", "type": "INTEGER"},
        ],
    })
    # Typed seed rows (data.json avoids CSV string-coercion noise in the test).
    _write(root / "tables" / "widgets" / "data.json", [
        {"label": "alpha", "qty": 3},
        {"label": "beta", "qty": 7},
    ])
    _write(root / "agents" / "greeter" / "greeter.json", {
        "name": "greeter",
        "instruction": "You greet people warmly.",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}},
        # Name-based grant on the widgets table — must round-trip and re-resolve.
        "permissions": {
            "grants": [
                {
                    "resource_type": "datastore_table",
                    "resource_name": "widgets",
                    "permission_ids": ["datastore.table.read"],
                }
            ]
        },
    })
    _write(root / "functions" / "echo" / "echo.json", {
        "name": "echo",
        "description": "Echoes its input.",
        "code": (
            "#input_type_name: EchoInput\n"
            "#output_type_name: EchoOutput\n"
            "#function_name: run_function\n"
            "from pydantic import BaseModel\n\n"
            "class EchoInput(BaseModel):\n    text: str\n\n"
            "class EchoOutput(BaseModel):\n    text: str\n\n"
            "def run_function(data: EchoInput) -> EchoOutput:\n"
            "    return EchoOutput(text=data.text)\n"
        ),
    })
    # Empty-graph workflow (skips graph validation) — exercises the create/list path.
    _write(root / "workflows" / "daily" / "daily.json", {
        "name": "daily",
        "description": "Daily routine.",
    })
    # TIME schedule targeting the agent by name (portable cross-ref). Works via
    # the test scheduler API server (see conftest).
    _write(root / "schedules" / "morning" / "morning.json", {
        "name": "morning",
        "schedule_type": "TIME",
        "agent_name": "greeter",
        "config": {"cron": "0 9 * * *"},
    })
    # NOTE: surfaces are wired too, but every platform needs a real runtime
    # dependency the e2e harness lacks (a public HTTPS webhook URL, or a
    # connector account / bot credentials), so they're not exercised here.
    # App with a prebuilt dist archive — uploads to READY without a build step.
    app_dir = root / "apps" / "mini"
    _write(app_dir / "mini.json", {"name": "mini", "public_slug": "mini", "description": "Mini app."})
    (app_dir / "dist.zip").write_bytes(_dist_zip())
    return root


async def _create_pod(client: AsyncClient, org: dict) -> str:
    suffix = uuid4().hex[:8]
    resp = await client.post("/pods", json={
        "name": f"Import E2E {suffix}",
        "slug": f"import-e2e-{suffix}",
        "type": "ASSISTANT",
        "organization_id": org["id"],
    })
    assert resp.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), resp.text
    return resp.json()["id"]


async def test_plan_and_apply_imports_resources(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    bundle = _stage_bundle(tmp_path / "bundle")
    archive = _zip_bundle(bundle)

    # 1) Plan: upload the bundle archive -> PLANNED with a step per resource.
    create = await authenticated_client.post(
        f"/pods/{pod_id}/imports",
        files={"bundle": ("import-e2e.zip", archive, "application/zip")},
        data={"source_name": "import-e2e"},
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    plan = create.json()
    assert plan["status"] == "PLANNED"
    assert {(s["resource_type"], s["resource_name"]) for s in plan["plan"]} == {
        ("tables", "widgets"),
        ("functions", "echo"),
        ("agents", "greeter"),
        ("workflows", "daily"),
        ("schedules", "morning"),
        ("apps", "mini"),
        # greeter carries grants, replayed in a deferred final pass once every
        # resource a grant could target exists.
        ("agent_grants", "greeter"),
    }
    assert plan["progress_total"] == 7
    import_id = plan["id"]

    # 2) Apply -> COMPLETED, every step done. No body: the bundle was staged.
    applied = await authenticated_client.post(
        f"/pods/{pod_id}/imports/{import_id}/apply",
    )
    assert applied.status_code == status.HTTP_200_OK, applied.text
    result = applied.json()
    assert result["status"] == "COMPLETED", result
    assert result["progress_done"] == 7
    assert all(s["status"] == "COMPLETED" for s in result["plan"])

    # 3) The resources really exist in the pod.
    agents = await authenticated_client.get(f"/pods/{pod_id}/agents")
    assert agents.status_code == status.HTTP_200_OK, agents.text
    assert any(a["name"] == "greeter" for a in agents.json().get("items", []))

    functions = await authenticated_client.get(f"/pods/{pod_id}/functions")
    assert functions.status_code == status.HTTP_200_OK, functions.text
    assert any(f["name"] == "echo" for f in functions.json().get("items", []))

    # 4) Seed rows landed.
    records = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/widgets/records"
    )
    assert records.status_code == status.HTTP_200_OK, records.text
    labels = {r.get("label") for r in records.json().get("items", [])}
    assert {"alpha", "beta"} <= labels

    # 5) Poll endpoint reflects the same terminal state.
    polled = await authenticated_client.get(f"/pods/{pod_id}/imports/{import_id}")
    assert polled.status_code == status.HTTP_200_OK
    assert polled.json()["status"] == "COMPLETED"


async def _import_and_apply(client: AsyncClient, pod_id: str, archive: bytes) -> dict:
    create = await client.post(
        f"/pods/{pod_id}/imports",
        files={"bundle": ("bundle.zip", archive, "application/zip")},
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    import_id = create.json()["id"]
    applied = await client.post(f"/pods/{pod_id}/imports/{import_id}/apply")
    assert applied.status_code == status.HTTP_200_OK, applied.text
    return applied.json()


async def test_export_then_reimport_roundtrip(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    # Seed pod A from a bundle.
    pod_a = await _create_pod(authenticated_client, fixed_test_org)
    seeded = await _import_and_apply(
        authenticated_client, pod_a, _zip_bundle(_stage_bundle(tmp_path / "bundle"))
    )
    assert seeded["status"] == "COMPLETED"

    # Export pod A -> a real downloadable zip.
    export = await authenticated_client.get(f"/pods/{pod_a}/export")
    assert export.status_code == status.HTTP_200_OK, export.text
    assert export.headers["content-type"].startswith("application/zip")
    assert export.headers["content-disposition"].endswith('.zip"')
    exported_archive = export.content
    assert exported_archive[:2] == b"PK"  # zip magic

    # Re-import the exported bundle into a fresh pod B.
    pod_b = await _create_pod(authenticated_client, fixed_test_org)
    result = await _import_and_apply(authenticated_client, pod_b, exported_archive)
    assert result["status"] == "COMPLETED", result
    names = {(s["resource_type"], s["resource_name"]) for s in result["plan"]}
    assert {
        ("tables", "widgets"),
        ("agents", "greeter"),
        ("functions", "echo"),
        ("workflows", "daily"),
        ("schedules", "morning"),
        ("apps", "mini"),
    } <= names

    # Pod B has the same resources + data — the round-trip is faithful.
    agents = await authenticated_client.get(f"/pods/{pod_b}/agents")
    assert any(a["name"] == "greeter" for a in agents.json().get("items", []))
    workflows = await authenticated_client.get(f"/pods/{pod_b}/workflows")
    assert any(w["name"] == "daily" for w in workflows.json().get("items", []))
    schedules = await authenticated_client.get(f"/pods/{pod_b}/schedules")
    assert any(s["name"] == "morning" for s in schedules.json().get("items", []))
    # App round-tripped with its dist archive — present and READY (built assets).
    apps = await authenticated_client.get(f"/pods/{pod_b}/apps")
    mini = next((a for a in apps.json().get("items", []) if a["name"] == "mini"), None)
    assert mini is not None, apps.text
    assert mini.get("status") == "READY"

    # The agent's table grant round-tripped and re-resolved to pod B's widgets.
    perms = await authenticated_client.get(f"/pods/{pod_b}/agents/greeter/permissions")
    assert perms.status_code == status.HTTP_200_OK, perms.text
    granted = {
        (g.get("resource_type"), g.get("resource_name"))
        for g in perms.json().get("grants", [])
    }
    assert ("datastore_table", "widgets") in granted
    functions = await authenticated_client.get(f"/pods/{pod_b}/functions")
    assert any(f["name"] == "echo" for f in functions.json().get("items", []))
    # Function code round-tripped: the schema was re-extracted on pod B, which
    # only happens if the exported bundle carried the code.
    echo_detail = await authenticated_client.get(f"/pods/{pod_b}/functions/echo")
    assert echo_detail.status_code == status.HTTP_200_OK, echo_detail.text
    assert echo_detail.json().get("input_schema")
    records = await authenticated_client.get(
        f"/pods/{pod_b}/datastore/tables/widgets/records"
    )
    labels = {r.get("label") for r in records.json().get("items", [])}
    assert {"alpha", "beta"} <= labels


async def test_import_into_new_pod_creates_pod_with_provenance(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    # The "create a new pod" path: POST /imports with no pod id creates a fresh
    # pod named from the bundle, stamps provenance, and returns a PLANNED import.
    archive = _zip_bundle(_stage_bundle(tmp_path / "bundle"))
    create = await authenticated_client.post(
        "/imports",
        files={"bundle": ("trumpet.zip", archive, "application/zip")},
        data={
            "organization_id": fixed_test_org["id"],
            "source_kind": "upload",
            "source_ref": "trumpet.zip",
        },
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text
    plan = create.json()
    assert plan["status"] == "PLANNED"
    new_pod_id = plan["pod_id"]

    pod = await authenticated_client.get(f"/pods/{new_pod_id}")
    assert pod.status_code == status.HTTP_200_OK, pod.text
    body = pod.json()
    assert body["name"] == "import-e2e"  # named from the bundle's pod.json
    source = body["config"]["source"]
    assert source["kind"] == "upload"
    assert source["ref"] == "trumpet.zip"


async def test_share_link_imports_existing_pod_into_a_new_pod(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    # The /import/p/<id> link: export an existing pod's bundle and create a fresh
    # pod from it for the caller, with provenance pointing back to the source.
    source_pod = await _create_pod(authenticated_client, fixed_test_org)
    seed = await authenticated_client.post(
        f"/pods/{source_pod}/imports",
        files={
            "bundle": ("seed.zip", _zip_bundle(_stage_bundle(tmp_path / "bundle")), "application/zip"),
        },
    )
    assert seed.status_code == status.HTTP_201_CREATED, seed.text
    applied = await authenticated_client.post(
        f"/pods/{source_pod}/imports/{seed.json()['id']}/apply"
    )
    assert applied.json()["status"] == "COMPLETED", applied.text

    created = await authenticated_client.post(f"/imports/from-pod/{source_pod}")
    assert created.status_code == status.HTTP_201_CREATED, created.text
    imp = created.json()
    assert imp["status"] == "PLANNED"
    new_pod_id = imp["pod_id"]
    assert new_pod_id != source_pod
    # provenance points back to the source pod
    src = (await authenticated_client.get(f"/pods/{new_pod_id}")).json()["config"]["source"]
    assert src["kind"] == "link"
    assert src["ref"] == source_pod
    # the new pod's plan mirrors the source's resources
    kinds = {(s["resource_type"], s["resource_name"]) for s in imp["plan"]}
    assert ("tables", "widgets") in kinds


async def test_github_badge_imports_a_repos_bundle_into_a_new_pod(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path, monkeypatch
):
    # The "Import to Lemma" badge on a published repo's README: fetching the
    # repo's zipball is mocked (no live network/GitHub dependency in tests),
    # but everything after that — naming, provenance, planning — is real.
    #
    # The mocked zipball is wrapped in an extra "<repo>-<ref>/" folder, exactly
    # like a real `codeload.github.com` zip -- on top of whatever the bundle
    # itself is wrapped in. A pod published before the wrapper-stripping fix
    # (or a repo built by hand) nests pod.json two folders deep; this must
    # still resolve to a real, non-empty plan rather than silently planning
    # zero resources (the empty-plan bug this regression-tests for looked like
    # a successful "PLANNED" import with nothing in it).
    from app.modules.pod_import.api.controllers import github_controller

    archive = _wrap_in_folder(_zip_bundle(_stage_bundle(tmp_path / "bundle")), "trumpet-4-2-main")

    async def fake_fetch(owner: str, repo: str, ref: str) -> bytes:
        assert (owner, repo, ref) == ("deepak", "trumpet", "HEAD")
        return archive

    monkeypatch.setattr(github_controller, "_fetch_repo_zip", fake_fetch)

    created = await authenticated_client.post(
        "/imports/from-github/deepak/trumpet",
        params={"organization_id": fixed_test_org["id"]},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    imp = created.json()
    assert imp["status"] == "PLANNED"

    # The plan must actually carry the bundle's resources, not come back empty.
    kinds = {(s["resource_type"], s["resource_name"]) for s in imp["plan"]}
    assert ("tables", "widgets") in kinds
    assert ("agents", "greeter") in kinds
    assert ("functions", "echo") in kinds

    pod = (await authenticated_client.get(f"/pods/{imp['pod_id']}")).json()
    assert pod["name"] == "import-e2e"  # named from the repo bundle's pod.json
    source = pod["config"]["source"]
    assert source["kind"] == "github"
    assert source["ref"] == "deepak/trumpet"


async def test_github_badge_imports_a_repos_bundle_into_an_existing_pod(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path, monkeypatch
):
    # The "install into this pod" counterpart: same badge/repo, but the caller
    # already has a pod open and wants the repo's resources added there rather
    # than getting a fresh pod.
    from app.modules.pod_import.api.controllers import github_controller

    target_pod = await _create_pod(authenticated_client, fixed_test_org)
    archive = _wrap_in_folder(_zip_bundle(_stage_bundle(tmp_path / "bundle")), "trumpet-4-2-main")

    async def fake_fetch(owner: str, repo: str, ref: str) -> bytes:
        assert (owner, repo, ref) == ("deepak", "trumpet", "HEAD")
        return archive

    monkeypatch.setattr(github_controller, "_fetch_repo_zip", fake_fetch)

    created = await authenticated_client.post(
        f"/pods/{target_pod}/imports/from-github/deepak/trumpet"
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    imp = created.json()
    assert imp["status"] == "PLANNED"
    assert imp["pod_id"] == target_pod  # installed into the existing pod, not a new one

    kinds = {(s["resource_type"], s["resource_name"]) for s in imp["plan"]}
    assert ("tables", "widgets") in kinds
    assert ("agents", "greeter") in kinds


async def test_github_publish_preview_renders_the_readme_without_touching_github(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    # The share dialog reads this before Publish is ever clicked, so it must
    # be real content (the actual capability counts) without requiring a
    # GitHub connection or creating anything remote.
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    seeded = await _import_and_apply(
        authenticated_client, pod_id, _zip_bundle(_stage_bundle(tmp_path / "bundle"))
    )
    assert seeded["status"] == "COMPLETED"

    resp = await authenticated_client.get(
        f"/pods/{pod_id}/export/github/preview", params={"repo_name": "my-cool-pod"}
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    body = resp.json()
    assert body["repo_name"] == "my-cool-pod"
    assert "# " in body["readme"]
    assert "Import-Lemma" in body["readme"]
    assert "table" in body["readme"]  # capability bullet from the seeded widgets table


async def test_create_new_pod_rejects_a_bundle_with_no_pod_json(
    authenticated_client: AsyncClient, fixed_test_org: dict, tmp_path: Path
):
    # A repo whose publish failed before anything committed (or any other
    # archive that isn't a real bundle) must error clearly, not silently
    # create an empty "Imported pod" with zero resources.
    empty = tmp_path / "empty"
    (empty / "not-a-bundle").mkdir(parents=True)
    (empty / "not-a-bundle" / "readme.txt").write_text("hello")
    archive = _zip_bundle(empty / "not-a-bundle")

    resp = await authenticated_client.post(
        "/imports",
        files={"bundle": ("nope.zip", archive, "application/zip")},
        data={"organization_id": fixed_test_org["id"]},
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, resp.text
