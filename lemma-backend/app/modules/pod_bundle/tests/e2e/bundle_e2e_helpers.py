"""Shared helpers for the pod-bundle *use-case* e2e tests.

These tests pack a real, hand-authored bundle from ``bundles/<name>/`` on disk,
import it through the URL-based flow (upload -> signed URL -> import -> apply),
and then *execute* the imported resources (run functions, fire schedules, run
workflows) to prove the import produced working — not just present — resources.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import status

from lemma_pod_bundle import pack_bundle

from app.modules.test_support.e2e.waiters import wait_for_status

BUNDLES_DIR = Path(__file__).parent / "bundles"


def pack_fixture_bundle(name: str) -> bytes:
    """Zip the on-disk fixture bundle ``bundles/<name>/`` into importable bytes."""
    root = BUNDLES_DIR / name
    assert (root / "pod.json").is_file(), f"no fixture bundle {name!r} at {root}"
    return pack_bundle(root)


async def provision_workspace(client, pod_id: str) -> None:
    """Warm the pod owner's workspace sandbox *through the backend* before an
    import runs a worker-created function against it.

    Why this exists: the shared e2e ``worker`` fixture is session-scoped and
    freezes ``API_URL`` at spawn, while ``backend_server`` binds a fresh port per
    test. A sandbox first provisioned by the import worker therefore bakes a
    callback URL that can't reach *this* test's backend, so the imported
    function's datastore calls fail with a network error. Provisioning the
    sandbox from the in-process backend first (which knows the current port)
    makes the worker reuse a reachable sandbox — and mirrors reality, where a
    user's workspace is already provisioned by the time they run an import.

    Creating a function triggers synchronous schema extraction, which is what
    provisions the sandbox; the probe is deleted afterwards.
    """
    probe = f"provision_probe_{uuid4().hex[:8]}"
    code = (
        "#input_type_name: In\n"
        "#output_type_name: Out\n"
        f"#function_name: {probe}\n\n"
        "from pydantic import BaseModel\n"
        "from lemma_sdk import FunctionContext\n\n"
        "class In(BaseModel):\n    x: int = 0\n\n"
        "class Out(BaseModel):\n    x: int = 0\n\n"
        f"async def {probe}(ctx: FunctionContext, data: In) -> Out:\n"
        "    return Out(x=data.x)\n"
    )
    created = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": probe, "description": "workspace warm-up probe", "code": code},
        follow_redirects=True,
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    await client.delete(f"/pods/{pod_id}/functions/{probe}")


async def new_pod(client, org_id: str, *, label: str = "Use Case") -> str:
    res = await client.post(
        "/pods",
        json={
            "name": f"{label} {uuid4()}",
            "slug": f"{label.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
    return res.json()["id"]


async def wait_import(client, pod_id: str, import_id: str, *, until, timeout: int = 120) -> dict:
    async def probe() -> dict:
        res = await client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    # failed=set() (not the wait_for_status default {"FAILED","ERROR"}):
    # `until` is caller-specified and can legitimately BE {"FAILED"} (see
    # test_connector_import_e2e.py, which waits for an import to reach its
    # own expected failure) -- preserve the original behavior of only ever
    # stopping on a status in `until`, nothing else.
    return await wait_for_status(
        label=f"pod {pod_id} bundle import {import_id} to reach {until}",
        probe=probe,
        expected=set(until),
        failed=set(),
        timeout_seconds=timeout,
        interval_seconds=0.15,
    )


async def start_and_plan_import(
    client, pod_id: str, zip_bytes: bytes, *, timeout: int = 120
) -> str:
    """Upload the zip, start the URL import, wait for the plan, and return the
    import_id (left AWAITING_CONFIRMATION so the caller can inspect/apply it)."""
    up = await client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    url = up.json()["url"]

    res = await client.post(
        f"/pods/{pod_id}/bundle/imports", json={"kind": "URL", "url": url}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]
    await wait_import(client, pod_id, import_id, until={"AWAITING_CONFIRMATION"}, timeout=timeout)
    return import_id


async def import_and_apply(
    client,
    pod_id: str,
    zip_bytes: bytes,
    *,
    variables: dict | None = None,
    timeout: int = 120,
) -> dict:
    """Full happy path: upload the zip, import from its signed URL, wait for the
    plan, apply (with any resolved ``variables``), and wait for COMPLETED. Returns
    the final import status body."""
    import_id = await start_and_plan_import(client, pod_id, zip_bytes, timeout=timeout)
    apply = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"variables": variables or {}},
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    final = await wait_import(
        client, pod_id, import_id, until={"COMPLETED", "FAILED"}, timeout=timeout
    )
    assert final["status"] == "COMPLETED", final
    return final


async def run_function(
    client,
    pod_id: str,
    function_name: str,
    input_data: dict,
    *,
    expected_status: str = "COMPLETED",
    timeout: int = 90,
) -> dict:
    """POST a function run and poll to a terminal state. Returns the run body."""
    res = await client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": input_data},
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_200_OK, res.text
    run_id = res.json()["id"]

    async def probe() -> dict:
        got = await client.get(
            f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
        )
        assert got.status_code == status.HTTP_200_OK, got.text
        return got.json()

    # failed=set(): the original loop treated COMPLETED and FAILED as
    # equally "done" and left the actual pass/fail decision to the assert
    # below against caller-supplied expected_status -- preserve that rather
    # than fail-fast on FAILED, which would break a caller expecting it.
    run = await wait_for_status(
        label=f"function {function_name} run {run_id}",
        probe=probe,
        expected={"COMPLETED", "FAILED"},
        failed=set(),
        timeout_seconds=timeout,
        interval_seconds=0.15,
    )
    assert run["status"] == expected_status, run
    return run
