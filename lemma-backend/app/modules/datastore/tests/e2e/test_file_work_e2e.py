"""Document processing on the work runtime, against a fake Kreuzberg.

The loop these tests exist for: one PDF the extractor panicked on with a 500,
classed "extractor unavailable", its attempt refunded and re-driven every
minute, forever -- and each failure counted against a circuit breaker that then
refused everybody else's documents. Now a failure on one document while the
extractor serves others is charged, counted once against the breaker, and
ends FAILED_PERMANENT after three attempts; an extractor that is actually down
is waited out without charging anyone, up to a time budget.

Everything goes through the public boundary (uploads, updates, renames and
searches over the API), and through the runtime's own claim, execute and reap.
The extractor is the fake HTTP server; nothing inside the subject is replaced.
"""

from __future__ import annotations

import io
import os
import signal
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pypdfium2 as pdfium
import pytest
from fastapi import status

from app.core.authorization.authz_epoch import pod_authz_epoch
from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis
from app.core.object_storage import local_object_storage_path
from app.core.infrastructure.work import (
    SYSTEM_NAMESPACE,
    WorkClass,
    WorkRegistry,
    WorkWriter,
)
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.events.storage_work import (
    PARENT_BACKFILL,
    STORAGE_COPY,
    STORAGE_RELEASE,
    register_storage_work,
)
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    get_kreuzberg_circuit,
    reset_kreuzberg_circuit,
)
from app.modules.datastore.tests.e2e.fake_document_processors import (
    FakeDocumentProcessorServer,
)
from app.modules.datastore.tests.e2e.file_work_support import (
    TEST_RETRY,
    FileWork,
    file_work_registry,
)
from app.modules.datastore.tests.e2e.harness import (
    DatastoreApi,
    build_pdf_bytes,
    invite_to_pod,
    signup_user,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

_PDF = "application/pdf"


@pytest.fixture
async def file_work(
    db_manager, fake_document_processor_server: FakeDocumentProcessorServer, monkeypatch
) -> AsyncIterator[FileWork]:
    for name, value in {
        # Writes enqueue processing, as in production; e2e turns that off
        # everywhere else and indexes in-process.
        "e2e_disable_worker_file_autoindex": False,
        "kreuzberg_url": fake_document_processor_server.base_url,
        "document_processor": "kreuzberg",
        "document_processing_debounce_seconds": 0,
        "kreuzberg_transient_retry_attempts": 1,
        "kreuzberg_transient_retry_base_delay_seconds": 0.01,
        "kreuzberg_connect_timeout_seconds": 0.5,
        "kreuzberg_request_timeout_seconds": 10.0,
        "kreuzberg_health_timeout_seconds": 1.0,
        "kreuzberg_circuit_failure_threshold": 3,
    }.items():
        monkeypatch.setattr(datastore_settings, name, value)
    reset_kreuzberg_circuit()
    yield FileWork(
        db_manager.session_factory, file_work_registry(), fake_document_processor_server
    )
    reset_kreuzberg_circuit()


def _pdf(*lines: str) -> bytes:
    return build_pdf_bytes(*lines)


def _two_page_pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(612, 792)
    pdf.new_page(612, 792)
    buffer = io.BytesIO()
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


async def _upload_pdf(api: DatastoreApi, name: str, *lines: str, **kwargs) -> dict:
    return await api.upload_file(
        name, _pdf(*(lines or (name,))), content_type=_PDF, **kwargs
    )


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_a_document_the_extractor_fails_on_ends_failed_after_three_attempts(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    """The 10k-reprocess loop, reproduced and closed."""
    poison = await _upload_pdf(pod_api, "poison-resolutions.pdf")
    healthy = [await _upload_pdf(pod_api, f"report-{n}.pdf") for n in range(4)]

    await file_work.drain()

    row = await file_work.row(poison["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"].startswith("document_unprocessable:")
    assert file_work.extractions("poison-resolutions.pdf") == 3
    (item,) = await file_work.items(poison["id"])
    assert (item["state"], item["attempts"], item["error_class"]) == (
        "dead",
        3,
        "attempts_exhausted",
    )
    for document in healthy:
        assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    # Three failures of one document are one failure to the breaker; the
    # threshold is three, so counting each would have opened it.
    assert not get_kreuzberg_circuit().is_open

    # A dead item is gone from the queue: nothing re-drives it.
    await file_work.drain()
    assert file_work.extractions("poison-resolutions.pdf") == 3


async def test_an_extractor_that_is_down_is_waited_out_without_charging(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(
        datastore_settings, "kreuzberg_url", f"http://127.0.0.1:{_closed_port()}"
    )
    document = await _upload_pdf(pod_api, "waiting.pdf")

    for _ in range(3):
        await file_work.run_once()
        await file_work.execute("UPDATE work_items SET run_at = now()")

    (item,) = await file_work.items(document["id"])
    assert item["state"] == "queued"
    assert item["attempts"] == 0
    assert item["error_class"] == "unavailable:connection_refused"
    assert item["first_unavailable_at"] is not None
    row = await file_work.row(document["id"])
    assert row["status"] == "PENDING"
    assert "Waiting for the document extractor" in row["last_processing_error"]

    # The outage outlasts the budget: the document is told so, and stops.
    await file_work.execute(
        "UPDATE work_items SET first_unavailable_at = now() - make_interval(secs => :s)",
        s=TEST_RETRY.unavailable_budget.total_seconds() + 60,
    )
    await file_work.drain()

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["attempts"], item["error_class"]) == (
        "dead",
        0,
        "unavailable_exhausted",
    )
    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"].startswith("extractor_unavailable:")


async def test_a_5xx_while_the_health_check_fails_is_not_charged(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    file_work.server.healthy = False
    document = await _upload_pdf(pod_api, "poison-while-down.pdf")

    for _ in range(4):
        await file_work.run_once()
        await file_work.execute("UPDATE work_items SET run_at = now()")

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["attempts"]) == ("queued", 0)
    assert item["error_class"] == "unavailable:health_check_failing"
    assert file_work.extractions("poison-while-down.pdf") == 4


async def test_the_same_bytes_again_are_not_reprocessed(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    content = _pdf("same bytes, twice")
    document = await pod_api.upload_file("twice.pdf", content, content_type=_PDF)
    poison = await _upload_pdf(pod_api, "poison-twice.pdf")
    await file_work.drain()
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"
    extracted = file_work.extractions("twice.pdf")

    await pod_api.update_file("/twice.pdf", content=content, filename="twice.pdf")
    await pod_api.update_file(
        "/poison-twice.pdf",
        content=_pdf("poison-twice.pdf"),
        filename="poison-twice.pdf",
    )

    assert await file_work.queued() == 0
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    # A permanent failure on these bytes stays one: rewriting them is not a retry.
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"
    await file_work.drain()
    assert file_work.extractions("twice.pdf") == extracted
    assert file_work.extractions("poison-twice.pdf") == 3

    # New bytes are new work.
    await pod_api.update_file(
        "/twice.pdf", content=_pdf("different now"), filename="twice.pdf"
    )
    await file_work.drain()
    assert file_work.extractions("twice.pdf") == extracted + 1


async def test_a_failed_reindex_leaves_the_previous_revision_searchable(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    document = await _upload_pdf(pod_api, "swap.pdf", "first revision")
    await file_work.drain()
    indexed = await file_work.row(document["id"])
    assert indexed["status"] == "COMPLETED"

    # The next revision's chunks cannot be stored: the insert fails inside the
    # transaction that would have replaced the old ones.
    file_work.server.nul_chunks = True
    await pod_api.update_file(
        "/swap.pdf", content=_pdf("second revision"), filename="swap.pdf"
    )
    await file_work.drain()

    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["indexed_sha256"] == indexed["content_sha256"]
    results = await pod_api.search_files("Deterministic extracted content")
    assert [hit["file_id"] for hit in results["items"]] == [document["id"]]


async def test_preflight_limits_fail_a_document_before_the_extractor_sees_it(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(datastore_settings, "document_processing_max_pages", 1)
    document = await pod_api.upload_file(
        "two-pages.pdf", _two_page_pdf(), content_type=_PDF
    )

    await file_work.drain()

    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"] == (
        "too_large: the PDF has 2 pages and documents over 1 pages are not processed"
    )
    assert file_work.extractions("two-pages.pdf") == 0


async def test_a_retry_is_allowed_once_per_cooldown(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    poison = await _upload_pdf(pod_api, "poison-retry.pdf")
    await file_work.drain()
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"

    retried = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path/retry-processing",
        params={"path": "/poison-retry.pdf"},
    )
    assert retried.status_code == status.HTTP_200_OK, retried.text
    assert retried.json()["status"] == "PENDING"
    await file_work.drain()
    assert file_work.extractions("poison-retry.pdf") == 6

    again = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path/retry-processing",
        params={"path": "/poison-retry.pdf"},
    )
    assert again.status_code == status.HTTP_429_TOO_MANY_REQUESTS, again.text
    assert again.json()["code"] == "DATASTORE_RETRY_TOO_SOON"


async def test_the_cutover_carries_no_file_over_and_a_retry_indexes_it(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    """The migration's seed replays nothing: every waiting or mid-extraction
    file ends FAILED with a note to retry, nothing is queued, and a retry is
    what processes it."""
    monkeypatch.setattr(datastore_settings, "e2e_disable_worker_file_autoindex", True)
    states = ["PENDING", "PROCESSING", "FAILED", "COMPLETED", "FAILED_PERMANENT"]
    rows = {
        state: await _upload_pdf(pod_api, f"legacy-{state.lower()}.pdf")
        for state in states
    }
    for state, row in rows.items():
        await file_work.execute(
            "UPDATE datastore_files SET status = :status WHERE id = :id",
            status=state,
            id=row["id"],
        )

    for _ in range(2):  # a re-run changes nothing more
        for statement in _seed_statements():
            await file_work.execute(statement)

    assert await file_work.queued() == 0
    for state, expected in (
        ("PENDING", "FAILED"),
        ("PROCESSING", "FAILED"),
        ("FAILED", "FAILED"),
        ("COMPLETED", "COMPLETED"),
        ("FAILED_PERMANENT", "FAILED_PERMANENT"),
    ):
        assert (await file_work.row(rows[state]["id"]))["status"] == expected, state
    for state in ("PENDING", "PROCESSING"):
        note = (await file_work.row(rows[state]["id"]))["last_processing_error"]
        assert "before the upgrade" in note, state

    monkeypatch.setattr(datastore_settings, "e2e_disable_worker_file_autoindex", False)
    retried = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path/retry-processing",
        params={"path": rows["PENDING"]["path"]},
    )
    assert retried.status_code == status.HTTP_200_OK, retried.text
    await file_work.drain()
    assert (await file_work.row(rows["PENDING"]["id"]))["status"] == "COMPLETED"


def _seed_statements() -> list[str]:
    import importlib.util

    path = (
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-14_work_runtime_0054.py"
    )
    spec = importlib.util.spec_from_file_location("work_runtime_0054", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed_statements()


async def test_a_rename_moves_no_bytes_once_the_object_layout_is_on(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(datastore_settings, "datastore_object_storage_layout", True)
    await pod_api.create_folder("/docs")
    document = await _upload_pdf(
        pod_api, "plan.pdf", "quarterly plan", directory_path="/docs"
    )
    await file_work.drain()
    row = await file_work.row(document["id"])
    assert (row["status"], row["storage_layout"]) == ("COMPLETED", "object")
    assert row["parent_id"] is not None
    root = local_object_storage_path("datastore")
    original = root / (
        f"pods/{pod_api.pod_id}/objects/{document['id']}/{row['content_sha256']}/content"
    )
    before = original.stat()

    await pod_api.update_file("/docs", new_path="/archive")

    moved = await file_work.row(document["id"])
    assert moved["path"] == "/archive/plan.pdf"
    after = original.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    files_root = root / f"pods/{pod_api.pod_id}/files"
    assert not files_root.exists() or not any(files_root.rglob("*plan.pdf*"))
    assert await pod_api.download_file("/archive/plan.pdf") == original.read_bytes()

    hits = await pod_api.search_files("Deterministic extracted", scope_path="/archive")
    assert [hit["file_id"] for hit in hits["items"]] == [document["id"]]
    assert [hit["path"] for hit in hits["items"]] == ["/archive/plan.pdf"]
    # The old folder is gone, from the index as from the tree.
    await pod_api.create_folder("/docs")
    missed = await pod_api.search_files("Deterministic extracted", scope_path="/docs")
    assert missed["items"] == []
    assert await file_work.queued() == 0


async def test_a_worker_killed_mid_extraction_loses_its_lease_and_the_file_finishes(
    pod_api: DatastoreApi, file_work: FileWork, e2e_settings, tmp_path
) -> None:
    """SIGKILL, not a shutdown: nothing is released, so only the lease says so."""
    file_work.server.hang = True
    document = await _upload_pdf(pod_api, "hang-contract.pdf")
    lease_seconds = 3

    log = tmp_path / "file-work-runner.log"
    with open(log, "w") as output:
        runner = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.modules.datastore.tests.e2e.file_work_runner",
                str(lease_seconds),
            ],
            cwd=str(Path(__file__).resolve().parents[5]),
            env={
                **os.environ,
                "DATABASE_URL": e2e_settings.database_url,
                "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url
                or e2e_settings.database_url,
                "REDIS_URL": e2e_settings.redis_url,
                "KREUZBERG_URL": file_work.server.base_url,
                "DOCUMENT_PROCESSOR": "kreuzberg",
                "KREUZBERG_REQUEST_TIMEOUT_SECONDS": "3600",
                "LOCAL_OBJECT_STORAGE_ROOT": settings.local_object_storage_root,
                "DOCUMENT_PROCESSING_LAYOUT_ENABLED": "false",
            },
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            try:
                await eventually(
                    label="the runner's extraction to reach the extractor",
                    probe=lambda: _extractions(file_work, "hang-contract.pdf"),
                    done=lambda count: count >= 1,
                    timeout_seconds=60,
                )
            except BaseException:
                print(log.read_text()[-6000:])
                raise
            os.kill(runner.pid, signal.SIGKILL)
            runner.wait(timeout=30)
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=30)
    assert runner.returncode == -signal.SIGKILL, log.read_text()[-4000:]
    (item,) = await file_work.items(document["id"])
    assert item["state"] == "running"
    assert (await file_work.row(document["id"]))["status"] == "PROCESSING"

    await eventually(
        label="the dead runner's lease to expire",
        probe=lambda: file_work.scalar(
            "SELECT bool_and(lease_until < now()) FROM work_items "
            "WHERE state = 'running'"
        ),
        done=bool,
        timeout_seconds=lease_seconds * 5,
    )
    file_work.server.hang = False
    await file_work.run_once()  # reaps: the lease loss is charged, not an attempt

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["lease_losses"], item["attempts"]) == ("queued", 1, 0)
    assert item["error_class"] == "lease_lost"

    await file_work.drain()
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert file_work.extractions("hang-contract.pdf") == 2


async def _extractions(file_work: FileWork, name: str) -> int:
    return file_work.extractions(name)


async def test_a_lease_longer_than_the_extraction_is_kept_alive_by_the_heartbeat(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    """``delayed-*`` takes longer than the lease; the heartbeat keeps it."""
    file_work.registry = file_work_registry(lease=timedelta(seconds=0.3))
    document = await _upload_pdf(pod_api, "delayed-long.pdf")

    await file_work.drain()

    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert file_work.extractions("delayed-long.pdf") == 1


# --- The search scope cache and the pod's authorization epoch ---------------


async def _epoch(api: DatastoreApi) -> str:
    epoch = await pod_authz_epoch(UUID(api.pod_id))
    assert epoch is not None, "the e2e Redis must be reachable"
    return epoch


async def test_every_change_to_who_may_read_what_moves_the_pods_epoch(
    pod_api: DatastoreApi,
    file_work: FileWork,
    authenticated_client,
    async_client,
    fixed_test_org,
) -> None:
    async def moved_by(action) -> None:
        before = await _epoch(pod_api)
        await action()
        assert await _epoch(pod_api) != before, action.__name__

    folder = await pod_api.create_folder("/team")
    uploaded: dict = {}

    async def create():
        uploaded.update(await _upload_pdf(pod_api, "notes.pdf", directory_path="/team"))

    async def completion():
        await file_work.drain()

    async def visibility():
        response = await pod_api.request(
            "PATCH",
            f"/pods/{pod_api.pod_id}/datastore/files/by-path",
            files=[
                ("path", (None, "/team/notes.pdf", None)),
                ("visibility", (None, "RESTRICTED", None)),
            ],
        )
        assert response.status_code == status.HTTP_200_OK, response.text

    async def move():
        await pod_api.update_file("/team/notes.pdf", new_path="/team/minutes.pdf")

    async def grant():
        response = await authenticated_client.put(
            f"/pods/{pod_api.pod_id}/roles/POD_VIEWER/permissions",
            json={
                "grants": [
                    {
                        "resource_type": "folder",
                        "resource_name": folder["path"],
                        "permission_ids": ["folder.read"],
                    }
                ]
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.text

    async def membership():
        viewer = await signup_user(async_client, "epoch-viewer")
        await invite_to_pod(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            pod_id=pod_api.pod_id,
            user=viewer,
            role="POD_VIEWER",
        )

    async def delete():
        await pod_api.delete_file("/team/minutes.pdf")

    for action in (create, completion, visibility, move, grant, membership, delete):
        await moved_by(action)


async def test_a_cached_search_scope_never_outlives_a_visibility_change(
    pod_api: DatastoreApi,
    file_work: FileWork,
    authenticated_client,
    async_client,
    fixed_test_org,
) -> None:
    """The readable side is sent as an exact filter, and its results are not
    re-authorized -- so a stale cache entry would show a document its reader
    just lost. The epoch is what retires it."""
    viewer = await signup_user(async_client, "scope-viewer")
    await invite_to_pod(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_api.pod_id,
        user=viewer,
        role="POD_VIEWER",
    )
    document = await _upload_pdf(pod_api, "budget.pdf", visibility="POD")
    # More the viewer may not read than may: the readable side is the shorter
    # one, so it is what gets sent.
    for n in range(3):
        await _upload_pdf(pod_api, f"board-{n}.pdf", visibility="RESTRICTED")
    await file_work.drain()
    reader = DatastoreApi(async_client, pod_api.pod_id, user=viewer)

    first = await reader.search_files("Deterministic extracted content")
    assert [hit["file_id"] for hit in first["items"]] == [document["id"]]
    cached = await _scope_cache_keys(pod_api.pod_id)
    assert cached, "the viewer's split was not cached"
    again = await reader.search_files("Deterministic extracted content")
    assert again["items"] == first["items"]

    response = await pod_api.request(
        "PATCH",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path",
        files=[
            ("path", (None, "/budget.pdf", None)),
            ("visibility", (None, "RESTRICTED", None)),
        ],
    )
    assert response.status_code == status.HTTP_200_OK, response.text

    after = await reader.search_files("Deterministic extracted content")
    assert after["items"] == []
    owner = await pod_api.search_files("Deterministic extracted content")
    assert document["id"] in {hit["file_id"] for hit in owner["items"]}


async def _scope_cache_keys(pod_id: str) -> list[str]:
    redis = get_redis(url=settings.redis_url)
    return [
        key async for key in redis.scan_iter(match=f"datastore:search-scope:{pod_id}:*")
    ]


def _admin_runner(file_work: FileWork) -> Callable[..., Awaitable[None]]:
    registry = WorkRegistry()
    register_storage_work(registry)

    async def run(kind: str | None = None, *, due: str | None = None) -> None:
        if kind is not None:
            async with file_work.session_maker() as session:
                await WorkWriter(session, registry=registry).add(kind, SYSTEM_NAMESPACE)
                await session.commit()
        if due is not None:
            assert await make_due(file_work.session_maker, kind=due)
        await run_until_idle(
            file_work.session_maker, registry=registry, classes=[WorkClass.ADMIN]
        )

    return run


async def _fetch_signed(pod_api: DatastoreApi, url: str):
    token = parse_qs(urlparse(url).query)["token"][0]
    return await pod_api.client.get("/public/datastore/files", params={"token": token})


async def test_the_backfills_fill_parents_and_move_bytes_to_the_object_layout(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    await pod_api.create_folder("/old")
    document = await _upload_pdf(pod_api, "kept.pdf", directory_path="/old")
    await file_work.drain()
    folder_id = (await file_work.row(document["id"]))["parent_id"]
    assert folder_id is not None
    # Rows written before the column existed.
    await file_work.execute("UPDATE datastore_files SET parent_id = NULL")
    run = _admin_runner(file_work)

    await run(PARENT_BACKFILL)
    assert (await file_work.row(document["id"]))["parent_id"] == folder_id

    row = await file_work.row(document["id"])
    root = local_object_storage_path("datastore") / f"pods/{pod_api.pod_id}"
    legacy = root / "files/old/kept.pdf"
    assert legacy.exists() and row["storage_layout"] == "path"
    await run(STORAGE_COPY)  # the switch is off: nothing moves
    assert (await file_work.row(document["id"]))["storage_layout"] == "path"

    # A link handed out before the copy names the path key.
    minted = (await pod_api.file_url("/old/kept.pdf"))["url"]

    monkeypatch.setattr(datastore_settings, "datastore_object_storage_layout", True)
    await run(STORAGE_COPY)

    assert (await file_work.row(document["id"]))["storage_layout"] == "object"
    moved = root / f"objects/{document['id']}/{row['content_sha256']}"
    original = await pod_api.download_file("/old/kept.pdf")
    assert (moved / "content").read_bytes() == original
    assert any((moved / "derived").iterdir())
    hits = await pod_api.search_files("Deterministic extracted", scope_path="/old")
    assert [hit["file_id"] for hit in hits["items"]] == [document["id"]]

    # The old keys outlive every link that could name them: the link minted
    # before the copy still serves the file.
    response = await _fetch_signed(pod_api, minted)
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.content == original
    assert legacy.exists()

    # Once those links have expired, the release drops the old keys.
    await run(due=STORAGE_RELEASE)
    assert not legacy.exists()
    assert not (root / "files/old/kept.pdf").exists()
    assert await pod_api.download_file("/old/kept.pdf") == original
    assert await file_work.queued() == 0


async def test_the_release_keeps_old_keys_a_path_layout_file_has_taken_over(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    """The delay is long enough for the path to be written again, with the
    switch off, by another file -- whose bytes are then at those keys."""
    document = await _upload_pdf(pod_api, "reused.pdf", "first edition")
    await file_work.drain()
    run = _admin_runner(file_work)
    monkeypatch.setattr(datastore_settings, "datastore_object_storage_layout", True)
    await run(STORAGE_COPY)
    assert (await file_work.row(document["id"]))["storage_layout"] == "object"

    await pod_api.update_file("/reused.pdf", new_path="/moved.pdf")
    monkeypatch.setattr(datastore_settings, "datastore_object_storage_layout", False)
    replacement = _pdf("second edition")
    await pod_api.upload_file("reused.pdf", replacement, content_type=_PDF)
    await file_work.drain()
    legacy = (
        local_object_storage_path("datastore")
        / f"pods/{pod_api.pod_id}/files/reused.pdf"
    )
    assert legacy.read_bytes() == replacement

    await run(due=STORAGE_RELEASE)

    assert legacy.read_bytes() == replacement
    assert await pod_api.download_file("/reused.pdf") == replacement
    assert await pod_api.download_file("/moved.pdf") != replacement


async def test_replacing_a_documents_bytes_replaces_what_search_knows(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    """Factory QA AC-PR979-3: words only the old revision had stop being found
    once the new revision is read, and the new words are found."""
    first = await pod_api.upload_file("echo-revision.md", b"zanzibar quokka original")
    await file_work.drain()
    found = await pod_api.search_files("zanzibar")
    assert first["id"] in {item["file_id"] for item in found["items"]}

    await pod_api.update_file(
        first["path"],
        content=b"kilimanjaro walrus replacement",
        filename="echo-revision.md",
    )
    await file_work.drain()

    assert (await file_work.row(first["id"]))["status"] == "COMPLETED"
    new = await pod_api.search_files("kilimanjaro")
    assert first["id"] in {item["file_id"] for item in new["items"]}
    old = await pod_api.search_files("zanzibar")
    assert old["items"] == [], "the replaced words are still searchable"
