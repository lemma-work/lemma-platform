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


# --- github ------------------------------------------------------------------


async def test_start_github_import_enqueues():
    uc, store, _, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = await uc.start_github_import(
        pod_id=pod_id,
        user_id=user_id,
        repo_url="https://github.com/acme/crm",
        owner=None,
        repo=None,
        ref="main",
    )
    assert state.status == ImportStatus.QUEUED
    assert state.source.kind == "github"
    assert state.source.repo_url.endswith("acme/crm")
    assert queue.calls[0][0] == "import_pod_github"
    assert queue.calls[0][1]["owner"] == "acme"
    assert queue.calls[0][1]["repo"] == "crm"


async def test_start_github_import_bad_ref_rejected():
    from app.modules.pod_bundle.domain.errors import BundleInvalidError

    uc, *_ = _use_cases()
    with pytest.raises(BundleInvalidError):
        await uc.start_github_import(
            pod_id=uuid4(),
            user_id=uuid4(),
            repo_url="not-a-repo!!",
            owner=None,
            repo=None,
            ref=None,
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
        source=BundleSource(kind="upload"),
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


async def test_cancel_aborts_and_deletes():
    uc, store, staging, queue = _use_cases()
    pod_id, user_id = uuid4(), uuid4()
    state = _awaiting_state(pod_id, user_id)
    await store.save_import(state)

    # FakeQueue has no abort; give it one that records calls.
    aborted = []
    queue.abort = lambda job_id, **kw: aborted.append(job_id) or True

    async def _abort(job_id, **kw):
        aborted.append(job_id)
        return True

    queue.abort = _abort
    staging.delete_archive = _noop_delete = _make_async_noop()

    await uc.cancel_import(pod_id=pod_id, import_id=state.import_id, user_id=user_id)
    assert await store.get_import(state.import_id) is None
    assert len(aborted) == 2  # plan + apply dedup ids


def _make_async_noop():
    async def _noop(*a, **k):
        return None

    return _noop
