"""Pod-bundle jobs as work items: what the request commits, cancel, dead, cutover.

Real Postgres, the real API in-process; work run in-process (``run_until_idle``)
where a test needs it, the pod's admin queue paused where a test needs an item
to stay queued. The crash and shutdown journeys with real worker processes are
``test_bundle_worker_e2e.py``; every kind's happy path on the session worker is
the rest of this folder.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    JobContext,
    PermanentFailure,
    WorkClass,
    WorkMode,
    WorkRegistry,
)
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.pod_bundle.application.export_use_cases import ExportUseCases
from app.modules.pod_bundle.domain.state import ExportState, ExportStatus
from app.modules.pod_bundle.events import work as bundle_work
from app.modules.pod_bundle.infrastructure.state_store import (
    get_pod_bundle_state_store,
)
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    new_pod,
    wait_import,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.pod_bundle.tests.e2e.bundle_work_support import (
    execute,
    item,
    pause_admin,
    rows,
    scalar,
    tables_bundle,
)

pytestmark = [pytest.mark.e2e]


async def _job_status(job_id: UUID) -> str | None:
    return await scalar("SELECT status FROM pod_bundle_jobs WHERE id = :id", id=job_id)


@pytest.mark.drives_work
async def test_an_export_request_commits_its_state_and_its_item_together(
    authenticated_client, fixed_test_org
) -> None:
    pod_id = await new_pod(authenticated_client, fixed_test_org["id"], label="Exp")
    await pause_admin(pod_id)
    res = await authenticated_client.post(f"/pods/{pod_id}/bundle/exports", json={})
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    export_id = UUID(res.json()["export_id"])

    assert await _job_status(export_id) == "QUEUED"
    assert await item(f"pod-export:{export_id}") == (
        "queued",
        0,
        0,
        "pod_bundle.export",
        f"pod:{pod_id}",
        f"pod_bundle:{pod_id}",
    )

    await pause_admin(pod_id, paused=False)
    # Run here, or by a session worker if one is up: either way it finishes.
    await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
    await eventually(
        label="the export finished",
        probe=lambda: _job_status(export_id),
        done=lambda found: found == ExportStatus.READY.value,
        timeout_seconds=60,
    )
    await eventually(
        label="its item is gone",
        probe=lambda: item(f"pod-export:{export_id}"),
        done=lambda found: found is None,
        timeout_seconds=30,
    )


class _QueueThatFailsAfterWriting(bundle_work.BundleJobQueue):
    async def queue_job(self, uow, kind, payload, *, pod_id, dedup_key) -> bool:
        await super().queue_job(uow, kind, payload, pod_id=pod_id, dedup_key=dedup_key)
        raise RuntimeError("the request died after writing both")


async def test_a_request_that_fails_leaves_neither_state_nor_item(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    pod_id = UUID(
        await new_pod(authenticated_client, fixed_test_org["id"], label="Atomic")
    )
    before = await scalar(
        "SELECT count(*) FROM pod_bundle_jobs WHERE pod_id = :p", p=pod_id
    )
    use_cases = ExportUseCases(
        SessionUnitOfWorkFactory(async_session_maker),
        job_queue=_QueueThatFailsAfterWriting(),
    )
    with pytest.raises(RuntimeError):
        await use_cases.start_export(
            pod_id=pod_id, user_id=UUID(str(fixed_test_user["id"])), include=None
        )
    assert (
        await scalar("SELECT count(*) FROM pod_bundle_jobs WHERE pod_id = :p", p=pod_id)
        == before
    )
    assert (
        await scalar(
            "SELECT count(*) FROM work_items "
            "WHERE ns = :ns AND kind LIKE 'pod_bundle.%'",
            ns=f"pod:{pod_id}",
        )
        == 0
    )


@pytest.mark.drives_work
async def test_a_second_identical_request_is_refused_while_the_first_is_queued(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    pod_id = UUID(
        await new_pod(authenticated_client, fixed_test_org["id"], label="Dup")
    )
    await pause_admin(str(pod_id))
    store = get_pod_bundle_state_store()
    user_id = UUID(str(fixed_test_user["id"]))
    export_id = uuid4()
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await store.stage_export(
            uow, ExportState(export_id=export_id, pod_id=pod_id, user_id=user_id)
        )
        payload = bundle_work.ExportJob(
            export_id=export_id, pod_id=pod_id, user_id=user_id
        )
        queue = bundle_work.BundleJobQueue()
        first = await queue.queue_job(
            uow,
            bundle_work.EXPORT,
            payload,
            pod_id=pod_id,
            dedup_key=bundle_work.export_key(export_id),
        )
        second = await queue.queue_job(
            uow,
            bundle_work.EXPORT,
            payload,
            pod_id=pod_id,
            dedup_key=bundle_work.export_key(export_id),
        )
    assert (first, second) == (True, False)
    await pause_admin(str(pod_id), paused=False)
    await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])


async def test_cancelling_a_queued_import_drops_its_item_and_ends_it(
    authenticated_client, fixed_test_org, tmp_path
) -> None:
    pod_id = await new_pod(authenticated_client, fixed_test_org["id"], label="Cnl")
    await pause_admin(pod_id)
    up = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("b.zip", tables_bundle(tmp_path, ["held"]), "application/zip")},
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports", json={"kind": "URL", "url": up.json()["url"]}
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]
    assert (await item(f"pod-import-plan:{import_id}"))[0] == "queued"

    cancel = await authenticated_client.delete(
        f"/pods/{pod_id}/bundle/imports/{import_id}"
    )
    assert cancel.status_code == status.HTTP_202_ACCEPTED, cancel.text
    assert cancel.json()["status"] == "CANCELLING"
    # Nothing was running it, so it ended in the cancel's own transaction.
    final = await wait_import(
        authenticated_client, pod_id, import_id, until={"CANCELLED"}, timeout=5
    )
    assert final["status"] == "CANCELLED"
    assert await item(f"pod-import-plan:{import_id}") is None
    await pause_admin(pod_id, paused=False)


@pytest.mark.drives_work
async def test_a_dead_item_ends_its_job_failed_in_the_dead_transaction(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    pod_id = UUID(
        await new_pod(authenticated_client, fixed_test_org["id"], label="Dead")
    )
    user_id = UUID(str(fixed_test_user["id"]))
    export_id = uuid4()
    registry = WorkRegistry()

    async def doomed(ctx: JobContext, payload: bundle_work.ExportJob) -> None:
        raise PermanentFailure("cannot run")

    # A kind no session worker knows, with the real dead hook: only this test
    # claims it.
    kind = "test.pod_bundle.doomed"
    registry.handler(
        kind=kind,
        cls=WorkClass.ADMIN,
        mode=WorkMode.JOB,
        payload=bundle_work.ExportJob,
        concurrency=1,
        on_dead=bundle_work.end_dead_job,
    )(doomed)
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        await get_pod_bundle_state_store().stage_export(
            uow, ExportState(export_id=export_id, pod_id=pod_id, user_id=user_id)
        )
        from app.core.infrastructure.work import WorkWriter, pod_namespace

        await WorkWriter(uow.session, registry=registry).add(
            kind,
            pod_namespace(pod_id),
            bundle_work.ExportJob(export_id=export_id, pod_id=pod_id, user_id=user_id),
            dedup_key=bundle_work.export_key(export_id),
            concurrency_key=bundle_work.concurrency_key(pod_id),
        )
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.ADMIN]
    )

    found = await rows(
        "SELECT status, error_code FROM pod_bundle_jobs WHERE id = :id", id=export_id
    )
    assert tuple(found[0]) == ("FAILED", "POD_BUNDLE_WORKER_INTERRUPTED")
    dead = await rows(
        "SELECT state, error_class FROM work_items WHERE dedup_key = :k",
        k=bundle_work.export_key(export_id),
    )
    assert tuple(dead[0]) == ("dead", "permanent")


async def test_the_sweep_leaves_a_job_with_a_live_item_alone(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    pod_id = UUID(
        await new_pod(authenticated_client, fixed_test_org["id"], label="Live")
    )
    await pause_admin(str(pod_id))
    user_id = UUID(str(fixed_test_user["id"]))
    store = get_pod_bundle_state_store()
    live, orphan = uuid4(), uuid4()
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        for export_id in (live, orphan):
            await store.stage_export(
                uow,
                ExportState(export_id=export_id, pod_id=pod_id, user_id=user_id),
            )
        await bundle_work.BundleJobQueue().queue_job(
            uow,
            bundle_work.EXPORT,
            bundle_work.ExportJob(export_id=live, pod_id=pod_id, user_id=user_id),
            pod_id=pod_id,
            dedup_key=bundle_work.export_key(live),
        )
    # Both look abandoned by their heartbeat.
    await execute(
        "UPDATE pod_bundle_jobs SET heartbeat_at = now() - interval '2 hours' "
        "WHERE id IN (:a, :b)",
        a=live,
        b=orphan,
    )
    from datetime import datetime, timezone

    recovered = await store.recover_stale_jobs(
        cutoff=datetime.now(timezone.utc) - timedelta(minutes=40)
    )
    recovered_ids = {getattr(state, "export_id", None) for state in recovered}
    assert orphan in recovered_ids and live not in recovered_ids
    assert await _job_status(live) == "QUEUED"
    assert await _job_status(orphan) == "FAILED"
    await execute("DELETE FROM work_items WHERE ns = :ns", ns=f"pod:{pod_id}")
    await pause_admin(str(pod_id), paused=False)


async def test_the_cutover_ends_every_unfinished_job_and_queues_none(
    authenticated_client, fixed_test_org, fixed_test_user
) -> None:
    """A job the old workers were running is not restarted by the release: it
    fails, saying why, and its owner starts it again. A finished job is left."""
    import importlib.util
    import json
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "agent_surface_bundle_work_0056",
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-14_agent_surface_bundle_work_0056.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    pod_id = UUID(
        await new_pod(authenticated_client, fixed_test_org["id"], label="Cut")
    )
    user_id = UUID(str(fixed_test_user["id"]))
    jobs = {
        "export": (uuid4(), "EXPORT", "EXPORTING"),
        "apply": (uuid4(), "IMPORT", "APPLYING"),
        "plan": (uuid4(), "IMPORT", "PLANNING"),
        "queued": (uuid4(), "IMPORT", "QUEUED"),
        "publish": (uuid4(), "PUBLISH", "PUBLISHING"),
        "done": (uuid4(), "EXPORT", "READY"),
    }
    for job_id, job_kind, job_status in jobs.values():
        await execute(
            "INSERT INTO pod_bundle_jobs (id, job_kind, pod_id, user_id, status, "
            "snapshot, version, attempt, heartbeat_at, committed_steps, "
            "created_at, updated_at) VALUES (:id, :kind, :pod, :user, :status, "
            "CAST(:snap AS jsonb), 1, 0, now(), '[]', now(), now())",
            id=job_id,
            kind=job_kind,
            pod=pod_id,
            user=user_id,
            status=job_status,
            snap=json.dumps({}),
        )

    for statement in migration.cutover_statements():
        await execute(statement)

    for name, (job_id, *_rest) in jobs.items():
        (status, code), *_ = await rows(
            "SELECT status, error_code FROM pod_bundle_jobs WHERE id = :id",
            id=job_id,
        )
        expected = (
            ("READY", None) if name == "done" else ("FAILED", "INTERRUPTED_BY_UPGRADE")
        )
        assert (status, code) == expected, name
    assert (
        await rows(
            "SELECT 1 FROM work_items WHERE ns = :ns AND kind LIKE 'pod_bundle.%'",
            ns=f"pod:{pod_id}",
        )
        == []
    )
