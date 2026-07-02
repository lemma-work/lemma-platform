"""Import use-case behavior with faked queue/store/staging (no DB, no Redis)."""

import zipfile
from io import BytesIO
from uuid import uuid4

import pytest

from app.modules.pod_bundle.application.import_use_cases import (
    ImportUseCases,
    import_plan_job_id,
)
from app.modules.pod_bundle.domain.errors import (
    BundleInvalidError,
    BundleJobExpiredError,
    BundleTooLargeError,
)
from app.modules.pod_bundle.domain.state import ImportState, ImportStatus


class FakeStore:
    def __init__(self):
        self.imports: dict = {}

    async def save_import(self, state: ImportState):
        state.touch()
        self.imports[state.import_id] = state

    async def get_import(self, import_id):
        return self.imports.get(import_id)


class FakeStaging:
    def __init__(self):
        self.puts: list = []

    async def put_archive(self, kind, job_id, data):
        self.puts.append((kind, job_id, len(data)))
        return f"{kind}/{job_id}/bundle.zip"


class FakeQueue:
    def __init__(self, *, duplicate=False):
        self.calls: list = []
        self._duplicate = duplicate

    async def enqueue(self, name, *, context, _job_id):
        self.calls.append((name, context, _job_id))
        return None if self._duplicate else object()


class FakeUow:
    def __init__(self):
        self.session = object()


class FakeUowFactory:
    def __call__(self):
        class _Ctx:
            async def __aenter__(self):
                return FakeUow()

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.fixture(autouse=True)
def _patch_auth(monkeypatch):
    """Neutralize the DB-backed authorization so use-case plumbing is testable
    without a database — the controller's PodEditorDep is the real guard."""

    async def _noop_authorize(self, *, pod_id, user_id, action):
        return None

    monkeypatch.setattr(ImportUseCases, "_authorize", _noop_authorize)


def _zip_bytes() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pod.json", '{"name": "x", "format_version": 2}')
    return buf.getvalue()


def _use_cases(**kw) -> tuple[ImportUseCases, FakeStore, FakeStaging, FakeQueue]:
    store, staging, queue = FakeStore(), FakeStaging(), FakeQueue(**kw)
    uc = ImportUseCases(
        FakeUowFactory(), state_store=store, staging=staging, job_queue=queue
    )
    return uc, store, staging, queue


async def test_start_upload_stages_and_enqueues():
    uc, store, staging, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()

    state = await uc.start_upload_import(
        pod_id=pod_id, user_id=user_id, filename="crm.zip", data=_zip_bytes()
    )

    assert state.status == ImportStatus.QUEUED
    assert state.source.kind == "upload"
    assert state.source.bundle_sha256
    assert staging.puts and staging.puts[0][0] == "pod-imports"
    assert queue.calls[0][0] == "plan_pod_import"
    assert queue.calls[0][2] == import_plan_job_id(state.import_id)
    assert store.imports[state.import_id].status == ImportStatus.QUEUED


async def test_start_upload_rejects_non_zip():
    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.start_upload_import(
            pod_id=uuid4(), user_id=uuid4(), filename="x.txt", data=b"not a zip"
        )


async def test_start_upload_rejects_oversize(monkeypatch):
    uc, *_ = _use_cases()
    from app.modules.pod_bundle.application import import_use_cases as m

    monkeypatch.setattr(m.pod_bundle_settings, "pod_bundle_max_archive_bytes", 4)
    with pytest.raises(BundleTooLargeError):
        await uc.start_upload_import(
            pod_id=uuid4(), user_id=uuid4(), filename="crm.zip", data=_zip_bytes()
        )


async def test_get_import_missing_raises_expired():
    uc, *_ = _use_cases()
    with pytest.raises(BundleJobExpiredError):
        await uc.get_import(pod_id=uuid4(), import_id=uuid4(), user_id=uuid4())


async def test_get_import_pod_mismatch_raises_expired():
    uc, store, staging, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_upload_import(
        pod_id=pod_id, user_id=user_id, filename="crm.zip", data=_zip_bytes()
    )
    # A different pod must not see this import (avoids cross-pod leakage).
    with pytest.raises(BundleJobExpiredError):
        await uc.get_import(pod_id=uuid4(), import_id=state.import_id, user_id=user_id)


async def test_duplicate_enqueue_raises_conflict():
    from app.modules.pod_bundle.domain.errors import BundleJobConflictError

    uc, *_ = _use_cases(duplicate=True)
    with pytest.raises(BundleJobConflictError):
        await uc.start_upload_import(
            pod_id=uuid4(), user_id=uuid4(), filename="crm.zip", data=_zip_bytes()
        )
