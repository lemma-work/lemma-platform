from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    BundleSourceKind,
    ImportPlan,
    ImportState,
    ImportStatus,
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)
from app.modules.pod_bundle.domain.errors import BundleStateConflictError
from app.modules.pod_bundle.events import handlers


def _state(
    status: ImportStatus,
    *,
    committed_steps: list[int] | None = None,
) -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind=BundleSourceKind.URL, url="https://lemma.test/b.zip"),
        status=status,
        current_step=4,
        committed_steps=committed_steps or [],
    )


class _Store:
    def __init__(self, state: ImportState | None) -> None:
        self.state = state
        self.saved: list[ImportState] = []

    async def get_import(self, import_id):
        assert self.state is None or import_id == self.state.import_id
        return self.state

    async def save_import(self, state):
        state.touch()
        self.state = state
        self.saved.append(state)


@pytest.mark.parametrize(
    "status",
    [
        ImportStatus.CANCELLING,
        ImportStatus.CANCELLED,
        ImportStatus.PARTIALLY_CANCELLED,
    ],
)
async def test_cancellation_requested_recognizes_every_stop_state(status):
    state = _state(status)

    assert (
        await handlers._cancellation_requested(_Store(state), state.import_id) is state
    )


async def test_cancellation_requested_ignores_missing_and_active_states():
    import_id = uuid4()
    assert await handlers._cancellation_requested(_Store(None), import_id) is None
    active = _state(ImportStatus.APPLYING)
    assert (
        await handlers._cancellation_requested(_Store(active), active.import_id) is None
    )


async def test_raise_if_cancelled_is_a_control_flow_boundary():
    cancelling = _state(ImportStatus.CANCELLING)
    with pytest.raises(handlers._ImportCancellation):
        await handlers._raise_if_cancelled(_Store(cancelling), cancelling.import_id)

    active = _state(ImportStatus.APPLYING)
    await handlers._raise_if_cancelled(_Store(active), active.import_id)


@pytest.mark.parametrize(
    ("committed_steps", "expected"),
    [([], ImportStatus.CANCELLED), ([1, 3], ImportStatus.PARTIALLY_CANCELLED)],
)
async def test_finalize_cancellation_is_terminal_and_reports_committed_steps(
    monkeypatch,
    committed_steps,
    expected,
):
    state = _state(ImportStatus.CANCELLING, committed_steps=committed_steps)
    store = _Store(state)
    staging = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(handlers, "publish_bundle_event", publish)

    await handlers._finalize_import_cancellation(store, staging, state)

    assert state.status is expected
    assert state.current_step is None
    assert state.completed_at is not None
    assert store.saved == [state]
    staging.delete_archive.assert_awaited_once_with("pod-imports", state.import_id)
    publish.assert_awaited_once()
    assert publish.await_args.args[1]["status"] == expected.value


async def test_finalize_cancellation_contains_staging_cleanup_failure(monkeypatch):
    state = _state(ImportStatus.CANCELLING)
    store = _Store(state)
    staging = AsyncMock()
    staging.delete_archive.side_effect = RuntimeError("object store unavailable")
    monkeypatch.setattr(handlers, "publish_bundle_event", AsyncMock())

    await handlers._finalize_import_cancellation(store, staging, state)

    assert state.status is ImportStatus.CANCELLED
    assert store.saved == [state]


async def test_finalize_cancellation_tolerates_another_worker_winning(monkeypatch):
    cancelling = _state(ImportStatus.CANCELLING)
    terminal = cancelling.model_copy(deep=True)
    terminal.status = ImportStatus.CANCELLED

    class ConflictStore:
        reads = 0

        async def get_import(self, import_id):
            assert import_id == cancelling.import_id
            self.reads += 1
            return cancelling if self.reads == 1 else terminal

        async def save_import(self, state):
            del state
            raise BundleStateConflictError()

    publish = AsyncMock()
    monkeypatch.setattr(handlers, "publish_bundle_event", publish)

    await handlers._finalize_import_cancellation(
        ConflictStore(),
        AsyncMock(),
        cancelling,
    )

    publish.assert_not_awaited()


async def test_apply_job_terminalizes_preexisting_cancelling_state(monkeypatch):
    state = _state(ImportStatus.CANCELLING)
    state.plan = SimplePlan()
    store = _Store(state)
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    staging = AsyncMock()
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    finalize.assert_awaited_once()


@pytest.mark.parametrize(
    ("task", "extra_context"),
    [
        (handlers.import_pod_github, {"owner": None, "repo": None}),
        (
            handlers.import_pod_url,
            {"source_kind": "pod-exports", "source_id": str(uuid4())},
        ),
    ],
)
async def test_fetch_jobs_terminalize_cancellation_control_flow(
    monkeypatch, task, extra_context
):
    state = _state(ImportStatus.QUEUED)
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_raise_if_cancelled",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)
    context = {"import_id": str(state.import_id), **extra_context}

    await task(context)

    finalize.assert_awaited_once_with(store, staging, state)


async def test_plan_job_terminalizes_cancellation_control_flow(monkeypatch):
    state = _state(ImportStatus.QUEUED)
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_plan_from_staging",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.plan_pod_import({"import_id": str(state.import_id)})

    finalize.assert_awaited_once_with(store, staging, state)


async def test_apply_job_catches_midflight_cancellation(monkeypatch):
    state = _state(ImportStatus.APPLYING)
    state.plan = SimplePlan()
    store = _Store(state)
    staging = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(
        handlers,
        "_raise_if_cancelled",
        AsyncMock(side_effect=handlers._ImportCancellation),
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    finalize.assert_awaited_once_with(store, staging, state)


async def test_app_step_cancellation_checkpoints_committed_step_before_terminalizing(
    monkeypatch,
):
    step = PlanStep(
        index=0,
        kind=StepKind.APP,
        name="dashboard",
        action=StepAction.CREATE,
    )
    state = _state(ImportStatus.APPLYING)
    state.plan = ImportPlan(format_version=1, steps=[step])
    store = _Store(state)
    staging = AsyncMock()
    staging.get_archive.return_value = b"zip-bytes"
    finalize = AsyncMock()
    app_runner = SimpleNamespace(run=AsyncMock())

    monkeypatch.setattr(
        handlers,
        "streaq_worker",
        SimpleNamespace(context=SimpleNamespace(uow_factory=object())),
    )
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: staging)
    monkeypatch.setattr(handlers, "_raise_if_cancelled", AsyncMock())
    monkeypatch.setattr(
        handlers, "_cancellation_requested", AsyncMock(return_value=state)
    )
    monkeypatch.setattr(handlers, "_finalize_import_cancellation", finalize)
    monkeypatch.setattr(handlers, "publish_bundle_event", AsyncMock())

    from app.modules.pod_bundle.infrastructure import app_builder

    monkeypatch.setattr(app_builder, "AppStepRunner", lambda *, uow_factory: app_runner)
    monkeypatch.setattr(
        "lemma_pod_bundle.extract_bundle", lambda *args, **kwargs: args[1]
    )

    await handlers.apply_pod_import(
        {
            "import_id": str(state.import_id),
            "pod_id": str(state.pod_id),
            "user_id": str(state.user_id),
        }
    )

    app_runner.run.assert_awaited_once()
    assert state.committed_steps == [0]
    assert step.status is StepStatus.DONE
    finalize.assert_awaited_once_with(store, staging, state)


@pytest.mark.parametrize(
    ("task", "extra_context"),
    [
        (handlers.import_pod_github, {"owner": None, "repo": None}),
        (
            handlers.import_pod_url,
            {"source_kind": "pod-exports", "source_id": str(uuid4())},
        ),
    ],
)
@pytest.mark.parametrize("state", [None, _state(ImportStatus.COMPLETED)])
async def test_fetch_jobs_skip_missing_and_terminal_state(
    monkeypatch, task, extra_context, state
):
    store = _Store(state)
    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", AsyncMock)
    import_id = state.import_id if state is not None else uuid4()

    await task({"import_id": str(import_id), **extra_context})


async def test_checkpoint_clears_current_step_and_reports_progress(monkeypatch):
    done = PlanStep(
        index=0,
        kind=StepKind.TABLE,
        name="customers",
        action=StepAction.CREATE,
        status=StepStatus.DONE,
    )
    skipped = PlanStep(
        index=1,
        kind=StepKind.APP,
        name="dashboard",
        action=StepAction.SKIP,
        status=StepStatus.SKIPPED,
    )
    state = _state(ImportStatus.APPLYING)
    state.plan = ImportPlan(format_version=1, steps=[done, skipped])
    store = _Store(state)
    publish = AsyncMock()
    monkeypatch.setattr(handlers, "publish_bundle_event", publish)

    await handlers._checkpoint(store, state, skipped)

    assert state.progress.done == 2
    assert state.progress.total == 2
    assert state.current_step is None
    assert publish.await_args.args[1]["step"]["status"] == StepStatus.SKIPPED.value


class SimplePlan:
    steps: list[object] = []
