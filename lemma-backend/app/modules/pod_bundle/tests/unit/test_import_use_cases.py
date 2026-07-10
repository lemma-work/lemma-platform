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
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
)


class FakeStore:
    def __init__(self):
        self.imports: dict = {}

    async def save_import(self, state: ImportState):
        state.touch()
        self.imports[state.import_id] = state

    async def reopen_import(self, state: ImportState):
        await self.save_import(state)

    async def get_import(self, import_id):
        return self.imports.get(import_id)

    async def delete_import(self, import_id):
        self.imports.pop(import_id, None)


class FakeStaging:
    def __init__(self):
        self.puts: list = []

    async def put_archive(self, kind, job_id, data):
        self.puts.append((kind, job_id, len(data)))
        return f"{kind}/{job_id}/bundle.zip"

    async def delete_archive(self, kind, job_id):
        return None


class FakeQueue:
    def __init__(self, *, duplicate=False):
        self.calls: list = []
        self._duplicate = duplicate

    async def enqueue(self, name, *, context, _job_id):
        self.calls.append((name, context, _job_id))
        return None if self._duplicate else object()

    async def abort(self, job_id, **kw):
        return True


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


from app.modules.pod_bundle.domain.state import BundleSourceKind  # noqa: E402


# --- uploads primitive -------------------------------------------------------


async def test_stage_upload_returns_signed_url():
    uc, _store, staging, _queue = _use_cases()
    url, expires_at = await uc.stage_upload(
        pod_id=uuid4(), user_id=uuid4(), filename="crm.zip", data=_zip_bytes()
    )
    assert "/pods/bundle/download?token=" in url
    assert expires_at is not None
    # Bytes were staged under pod-imports; no import created yet.
    assert staging.puts and staging.puts[0][0] == "pod-imports"


async def test_stage_upload_rejects_non_zip():
    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.stage_upload(
            pod_id=uuid4(), user_id=uuid4(), filename="x.txt", data=b"not a zip"
        )


async def test_stage_upload_rejects_oversize(monkeypatch):
    uc, *_ = _use_cases()
    from app.modules.pod_bundle.application import import_use_cases as m

    monkeypatch.setattr(m.pod_bundle_settings, "pod_bundle_max_archive_bytes", 4)
    with pytest.raises(BundleTooLargeError):
        await uc.stage_upload(
            pod_id=uuid4(), user_id=uuid4(), filename="crm.zip", data=_zip_bytes()
        )


# --- start_import: URL -------------------------------------------------------


async def test_start_import_url_verifies_token_and_enqueues():
    uc, store, _, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    # A real lemma download URL for a staged upload.
    url, _ = await uc.stage_upload(
        pod_id=pod_id, user_id=user_id, filename="crm.zip", data=_zip_bytes()
    )
    state = await uc.start_import(
        pod_id=pod_id, user_id=user_id, kind=BundleSourceKind.URL, url=url
    )
    assert state.status == ImportStatus.QUEUED
    assert state.source.kind == BundleSourceKind.URL
    assert state.source.url == url
    call = next(c for c in queue.calls if c[0] == "import_pod_url")
    assert call[1]["source_kind"] == "pod-imports"
    assert call[1]["source_id"]
    assert call[2] == import_plan_job_id(state.import_id)


async def test_start_import_url_non_lemma_url_rejected():
    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.start_import(
            pod_id=uuid4(),
            user_id=uuid4(),
            kind=BundleSourceKind.URL,
            url="https://evil.example.com/bundle.zip",
        )


async def test_start_import_url_bad_token_rejected():
    uc, *_ = _use_cases()
    with pytest.raises(BundleJobExpiredError):
        await uc.start_import(
            pod_id=uuid4(),
            user_id=uuid4(),
            kind=BundleSourceKind.URL,
            url="http://localhost:8711/pods/bundle/download?token=garbage",
        )


async def test_start_import_url_missing_url_rejected():
    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.start_import(
            pod_id=uuid4(), user_id=uuid4(), kind=BundleSourceKind.URL, url=None
        )


# --- start_import: GITHUB ----------------------------------------------------


async def test_start_import_github_enqueues():
    uc, store, _, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_import(
        pod_id=pod_id,
        user_id=user_id,
        kind=BundleSourceKind.GITHUB,
        url="https://github.com/acme/crm",
        ref="main",
    )
    assert state.status == ImportStatus.QUEUED
    assert state.source.kind == BundleSourceKind.GITHUB
    assert state.source.repo_url.endswith("acme/crm")
    call = next(c for c in queue.calls if c[0] == "import_pod_github")
    assert call[1]["owner"] == "acme" and call[1]["repo"] == "crm"


async def test_start_import_github_bad_repo_rejected():
    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.start_import(
            pod_id=uuid4(), user_id=uuid4(), kind=BundleSourceKind.GITHUB, url="not-a-repo!!"
        )


async def test_get_import_missing_raises_expired():
    uc, *_ = _use_cases()
    with pytest.raises(BundleJobExpiredError):
        await uc.get_import(pod_id=uuid4(), import_id=uuid4(), user_id=uuid4())


async def test_get_import_pod_mismatch_raises_expired():
    uc, store, _, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_import(
        pod_id=pod_id, user_id=user_id, kind=BundleSourceKind.GITHUB,
        url="https://github.com/acme/crm",
    )
    with pytest.raises(BundleJobExpiredError):
        await uc.get_import(pod_id=uuid4(), import_id=state.import_id, user_id=user_id)


async def test_duplicate_enqueue_raises_conflict():
    from app.modules.pod_bundle.domain.errors import BundleJobConflictError

    uc, *_ = _use_cases(duplicate=True)
    with pytest.raises(BundleJobConflictError):
        await uc.start_import(
            pod_id=uuid4(), user_id=uuid4(), kind=BundleSourceKind.GITHUB,
            url="https://github.com/acme/crm",
        )


# --- apply -------------------------------------------------------------------


def _awaiting_state(pod_id, user_id, *, steps=None, variables=None):
    from app.modules.pod_bundle.domain.state import (
        ImportPlan,
        PlanStep,
        StepAction,
        StepKind,
    )

    plan = ImportPlan(
        format_version=2,
        steps=steps
        or [PlanStep(index=0, kind=StepKind.TABLE, name="leads", action=StepAction.CREATE)],
        variables=variables or [],
    )
    return ImportState(
        import_id=uuid4(),
        pod_id=pod_id,
        user_id=user_id,
        source=BundleSource(kind="URL"),
        status=ImportStatus.AWAITING_CONFIRMATION,
        plan=plan,
    )


async def test_apply_enqueues_with_dedup_id():
    from app.modules.pod_bundle.application.import_use_cases import import_apply_job_id

    uc, store, _, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(pod_id, user_id)
    await store.save_import(state)

    result = await uc.apply_import(pod_id=pod_id, import_id=state.import_id, user_id=user_id)

    assert result.status == ImportStatus.APPLYING
    assert queue.calls[0][0] == "apply_pod_import"
    assert queue.calls[0][2] == import_apply_job_id(state.import_id)


async def test_explicit_apply_reopens_failed_job_and_increments_attempt():
    uc, store, _, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(pod_id, user_id)
    state.status = ImportStatus.FAILED
    state.error = "Previous attempt failed."
    state.completed_at = state.updated_at
    await store.save_import(state)

    result = await uc.apply_import(
        pod_id=pod_id,
        import_id=state.import_id,
        user_id=user_id,
    )

    assert result.status is ImportStatus.APPLYING
    assert result.attempt == 2
    assert result.error is None
    assert result.completed_at is None


async def test_apply_wrong_status_conflicts():
    from app.modules.pod_bundle.domain.errors import BundleJobConflictError

    uc, store, _, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(pod_id, user_id)
    state.status = ImportStatus.PLANNING
    await store.save_import(state)
    with pytest.raises(BundleJobConflictError):
        await uc.apply_import(pod_id=pod_id, import_id=state.import_id, user_id=user_id)


async def test_apply_destructive_requires_confirmation():
    from app.modules.pod_bundle.domain.errors import BundleConfirmationRequiredError
    from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind

    uc, store, _, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(
        pod_id,
        user_id,
        steps=[
            PlanStep(
                index=0,
                kind=StepKind.TABLE,
                name="leads",
                action=StepAction.UPDATE,
                destructive=True,
            )
        ],
    )
    await store.save_import(state)
    with pytest.raises(BundleConfirmationRequiredError):
        await uc.apply_import(pod_id=pod_id, import_id=state.import_id, user_id=user_id)
    # With confirmation it proceeds.
    ok = await uc.apply_import(
        pod_id=pod_id, import_id=state.import_id, user_id=user_id, confirm_destructive=True
    )
    assert ok.status == ImportStatus.APPLYING


async def test_apply_missing_required_variable():
    from app.modules.pod_bundle.domain.errors import BundleConfirmationRequiredError
    from app.modules.pod_bundle.domain.state import VariableSpec

    uc, store, _, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(
        pod_id, user_id, variables=[VariableSpec(name="region", kind="free", required=True)]
    )
    await store.save_import(state)
    with pytest.raises(BundleConfirmationRequiredError):
        await uc.apply_import(pod_id=pod_id, import_id=state.import_id, user_id=user_id)


async def test_cancel_idle_import_persists_terminal_tombstone():
    uc, store, staging, _ = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(pod_id, user_id)
    await store.save_import(state)

    deleted = []

    async def _delete(kind, import_id):
        deleted.append((kind, import_id))

    staging.delete_archive = _delete

    result = await uc.cancel_import(
        pod_id=pod_id, import_id=state.import_id, user_id=user_id
    )
    persisted = await store.get_import(state.import_id)
    assert result.status == ImportStatus.CANCELLING
    assert persisted is not None
    assert persisted.status == ImportStatus.CANCELLED
    assert persisted.cancel_requested_at is not None
    assert persisted.completed_at is not None
    assert deleted == [("pod-imports", state.import_id)]


def _make_async_noop():
    async def _noop(*a, **k):
        return None

    return _noop
