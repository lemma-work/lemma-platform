"""Pod-bundle jobs on a real worker process: killed, and shut down.

The API (this process) starts the import and applies it; ``python -m
app.worker`` runs the jobs from the items those requests wrote. Nothing is
pumped by the test. Each apply step is slowed (``bundle_worker_entrypoint``)
so the worker dies, or is told to stop, between two committed steps:

* **SIGKILL**: the job's lease runs out and a second worker resumes the apply
  from the first pending step -- every table exists exactly once, the job
  completes once;
* **SIGTERM**: the worker hands the job back at the next step boundary,
  uncharged (no attempt, no lost lease), and the next worker finishes it.
"""

from __future__ import annotations

import os
import signal
import time
from contextlib import asynccontextmanager

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    new_pod,
    start_and_plan_import,
    wait_import,
)
from app.modules.pod_bundle.tests.e2e.bundle_work_support import (
    item,
    table_names,
    tables_bundle,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process

# Slow and in the protected lane: each test owns its worker processes, which
# must not share an e2e process with the session-scoped base worker.
pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.drives_work]

LEASE_SECONDS = 3.0
TABLES = [f"work_table_{n}" for n in range(6)]


@asynccontextmanager
async def _worker(e2e_settings, name: str, *, step_delay: float):
    async with production_worker_process(
        e2e_settings,
        log_prefix=f"bundle_worker_{name}",
        worker_entrypoint="app.modules.pod_bundle.tests.e2e.bundle_worker_entrypoint",
        extra_env={
            "E2E_BUNDLE_LEASE_SECONDS": str(LEASE_SECONDS),
            "E2E_BUNDLE_STEP_DELAY_SECONDS": str(step_delay),
            "WORK_POLL_INTERVAL_SECONDS": "0.2",
        },
    ) as worker:
        yield worker


async def _committed(client, pod_id: str, import_id: str) -> dict:
    res = await client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()


async def _apply_until_two_steps_landed(client, pod_id, import_id) -> None:
    apply = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    await eventually(
        label="two apply steps committed",
        probe=lambda: _committed(client, pod_id, import_id),
        done=lambda s: len(s.get("committed_steps") or []) >= 2,
        timeout_seconds=60,
        interval_seconds=0.1,
    )


async def test_a_killed_worker_apply_resumes_from_its_first_pending_step(
    e2e_settings, authenticated_client, fixed_test_org, tmp_path
) -> None:
    pod_id = await new_pod(authenticated_client, fixed_test_org["id"], label="Kill")
    async with _worker(e2e_settings, "killed", step_delay=1.0) as first:
        import_id = await start_and_plan_import(
            authenticated_client, pod_id, tables_bundle(tmp_path, TABLES)
        )
        await _apply_until_two_steps_landed(authenticated_client, pod_id, import_id)
        os.kill(first.process.pid, signal.SIGKILL)
        first.process.wait(timeout=10)
        killed_at = time.monotonic()

    held = await item(f"pod-import:{import_id}")
    assert held is not None and held[0] == "running", "held by a dead worker"
    assert held[3:] == (
        "pod_bundle.apply",
        f"pod:{pod_id}",
        f"pod_bundle:{pod_id}",
    )
    before = await _committed(authenticated_client, pod_id, import_id)
    assert before["status"] == "APPLYING"
    assert 2 <= len(before["committed_steps"]) < len(TABLES)

    async with _worker(e2e_settings, "resumer", step_delay=0) as second:
        ready_at = time.monotonic()
        final = await wait_import(
            authenticated_client,
            pod_id,
            import_id,
            until={"COMPLETED", "FAILED"},
            timeout=60,
        )
        finished_at = time.monotonic()
        log = second.log_path.read_text()

    print(
        f"\nSIGKILL after {len(before['committed_steps'])}/{len(TABLES)} steps -> "
        f"completed {finished_at - killed_at:.1f}s after the kill, "
        f"{finished_at - ready_at:.1f}s after the next worker was ready"
    )
    assert final["status"] == "COMPLETED", final
    assert sorted(final["committed_steps"]) == list(range(len(final["plan"]["steps"])))
    # Every table exactly once: the steps that landed before the kill were not
    # applied again into duplicates, and the one in flight converged.
    assert await table_names(pod_id) == sorted(TABLES)
    assert await item(f"pod-import:{import_id}") is None
    assert '"event": "work.lease.reaped"' in log


async def test_a_stopping_worker_hands_the_apply_back_uncharged(
    e2e_settings, authenticated_client, fixed_test_org, tmp_path
) -> None:
    pod_id = await new_pod(authenticated_client, fixed_test_org["id"], label="Term")
    async with _worker(e2e_settings, "stopped", step_delay=1.0) as first:
        import_id = await start_and_plan_import(
            authenticated_client, pod_id, tables_bundle(tmp_path, TABLES)
        )
        await _apply_until_two_steps_landed(authenticated_client, pod_id, import_id)
        first.process.send_signal(signal.SIGTERM)
        signalled_at = time.monotonic()
        released = await eventually(
            label="the apply handed back",
            probe=lambda: item(f"pod-import:{import_id}"),
            done=lambda found: found is not None and found[0] == "queued",
            timeout_seconds=30,
            interval_seconds=0.1,
        )
        released_after = time.monotonic() - signalled_at
        first.process.wait(timeout=30)

    print(f"\nSIGTERM -> apply handed back in {released_after:.2f}s")
    assert released[1:3] == (0, 0), "a shutdown is neither an attempt nor a lost lease"
    async with _worker(e2e_settings, "next", step_delay=0):
        final = await wait_import(
            authenticated_client,
            pod_id,
            import_id,
            until={"COMPLETED", "FAILED"},
            timeout=60,
        )
    assert final["status"] == "COMPLETED", final
    assert await table_names(pod_id) == sorted(TABLES)
