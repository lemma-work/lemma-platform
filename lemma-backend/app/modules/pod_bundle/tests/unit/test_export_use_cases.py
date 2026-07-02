"""Export use-case semantics with a fake queue + fake store (no DB, no Redis)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.modules.pod_bundle.application.export_use_cases as uc_mod
from app.modules.pod_bundle.application.export_use_cases import (
    ExportUseCases,
    export_job_id,
)
from app.modules.pod_bundle.domain.errors import (
    BundleJobConflictError,
    BundleJobExpiredError,
    BundleStagingMissingError,
)
from app.modules.pod_bundle.domain.state import ExportState, ExportStatus


# --- fakes -------------------------------------------------------------------


class _FakeUow:
    def __init__(self):
        self.session = object()


@asynccontextmanager
async def _fake_uow_ctx():
    yield _FakeUow()


class _FakeUowFactory:
    def __call__(self):
        return _fake_uow_ctx()


class _FakeStore:
    def __init__(self):
        self.exports: dict = {}

    async def save_export(self, state: ExportState) -> None:
        state.touch()
        self.exports[state.export_id] = state

    async def get_export(self, export_id):
        return self.exports.get(export_id)

    async def delete_export(self, export_id):
        self.exports.pop(export_id, None)


class _FakeQueue:
    def __init__(self, *, return_none=False):
        self.calls: list[dict] = []
        self._return_none = return_none

    async def enqueue(self, job_name, *, context=None, _job_id=None):
        self.calls.append({"job_name": job_name, "context": context, "_job_id": _job_id})
        if self._return_none:
            return None
        return SimpleNamespace(id=_job_id)


class _FakeStaging:
    def __init__(self, *, iterator=object()):
        self._iterator = iterator

    async def iter_archive(self, kind, job_id):
        return self._iterator


class _FakeCtx:
    def __init__(self):
        self.required: list = []

    async def require(self, permission, resource=None):
        self.required.append(permission)


@pytest.fixture(autouse=True)
def _patch_authz(monkeypatch):
    """Replace AuthorizationDataService so authorize is a no-op (no DB)."""

    class _FakeAuthz:
        def __init__(self, session):
            pass

        async def build_user_context(self, *, user_id, pod_id):
            return _FakeCtx()

    monkeypatch.setattr(uc_mod, "AuthorizationDataService", _FakeAuthz)


def _use_cases(*, store=None, queue=None, staging=None) -> ExportUseCases:
    return ExportUseCases(
        _FakeUowFactory(),
        state_store=store or _FakeStore(),
        staging=staging or _FakeStaging(),
        job_queue=queue or _FakeQueue(),
    )


# --- start_export ------------------------------------------------------------


async def test_start_export_saves_queued_and_enqueues_with_dedup_id():
    store, queue = _FakeStore(), _FakeQueue()
    use_cases = _use_cases(store=store, queue=queue)
    pod_id, user_id = uuid4(), uuid4()

    state = await use_cases.start_export(
        pod_id=pod_id, user_id=user_id, with_data=True, include=None
    )

    assert state.status == ExportStatus.QUEUED
    assert state.with_data is True
    # Persisted under its export_id.
    saved = store.exports[state.export_id]
    assert saved.status == ExportStatus.QUEUED

    # Enqueued once with the deterministic dedup id + string-coerced context.
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["job_name"] == "export_pod_bundle"
    assert call["_job_id"] == export_job_id(state.export_id)
    assert call["context"] == {
        "export_id": str(state.export_id),
        "pod_id": str(pod_id),
        "user_id": str(user_id),
    }


async def test_start_export_passes_include_through():
    store = _FakeStore()
    use_cases = _use_cases(store=store)
    state = await use_cases.start_export(
        pod_id=uuid4(), user_id=uuid4(), with_data=False, include=["tables", "agents"]
    )
    assert store.exports[state.export_id].include == ["tables", "agents"]
    assert store.exports[state.export_id].with_data is False


async def test_start_export_duplicate_enqueue_raises_conflict():
    use_cases = _use_cases(queue=_FakeQueue(return_none=True))
    with pytest.raises(BundleJobConflictError):
        await use_cases.start_export(
            pod_id=uuid4(), user_id=uuid4(), with_data=True, include=None
        )


# --- get_export --------------------------------------------------------------


async def test_get_export_returns_state():
    store = _FakeStore()
    use_cases = _use_cases(store=store)
    pod_id = uuid4()
    state = ExportState(export_id=uuid4(), pod_id=pod_id, user_id=uuid4())
    await store.save_export(state)

    loaded = await use_cases.get_export(
        pod_id=pod_id, export_id=state.export_id, user_id=state.user_id
    )
    assert loaded.export_id == state.export_id


async def test_get_export_missing_raises_expired():
    use_cases = _use_cases()
    with pytest.raises(BundleJobExpiredError):
        await use_cases.get_export(
            pod_id=uuid4(), export_id=uuid4(), user_id=uuid4()
        )


async def test_get_export_wrong_pod_raises_expired():
    store = _FakeStore()
    use_cases = _use_cases(store=store)
    state = ExportState(export_id=uuid4(), pod_id=uuid4(), user_id=uuid4())
    await store.save_export(state)
    # A different pod_id must not see another pod's export.
    with pytest.raises(BundleJobExpiredError):
        await use_cases.get_export(
            pod_id=uuid4(), export_id=state.export_id, user_id=state.user_id
        )


# --- open_download -----------------------------------------------------------


async def test_open_download_ready_returns_iterator():
    store = _FakeStore()
    sentinel = object()
    use_cases = _use_cases(store=store, staging=_FakeStaging(iterator=sentinel))
    pod_id = uuid4()
    state = ExportState(
        export_id=uuid4(),
        pod_id=pod_id,
        user_id=uuid4(),
        status=ExportStatus.READY,
        bundle_filename="crm.zip",
    )
    await store.save_export(state)

    filename, iterator = await use_cases.open_download(
        pod_id=pod_id, export_id=state.export_id, user_id=state.user_id
    )
    assert filename == "crm.zip"
    assert iterator is sentinel


async def test_open_download_not_ready_raises_conflict():
    store = _FakeStore()
    use_cases = _use_cases(store=store)
    pod_id = uuid4()
    state = ExportState(
        export_id=uuid4(), pod_id=pod_id, user_id=uuid4(), status=ExportStatus.EXPORTING
    )
    await store.save_export(state)
    with pytest.raises(BundleJobConflictError):
        await use_cases.open_download(
            pod_id=pod_id, export_id=state.export_id, user_id=state.user_id
        )


async def test_open_download_swept_archive_raises_staging_missing():
    store = _FakeStore()
    use_cases = _use_cases(store=store, staging=_FakeStaging(iterator=None))
    pod_id = uuid4()
    state = ExportState(
        export_id=uuid4(),
        pod_id=pod_id,
        user_id=uuid4(),
        status=ExportStatus.READY,
        bundle_filename="crm.zip",
    )
    await store.save_export(state)
    with pytest.raises(BundleStagingMissingError):
        await use_cases.open_download(
            pod_id=pod_id, export_id=state.export_id, user_id=state.user_id
        )
