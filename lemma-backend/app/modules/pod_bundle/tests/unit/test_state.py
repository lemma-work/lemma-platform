"""Domain state document behavior."""

from uuid import uuid4

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ExportState,
    ExportStatus,
    ImportPlan,
    ImportState,
    ImportStatus,
    PlanStep,
    PublishState,
    PublishStatus,
    StepAction,
    StepKind,
    StepStatus,
)


def _import_state(**overrides) -> ImportState:
    defaults = dict(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind="upload", bundle_filename="crm.zip"),
    )
    defaults.update(overrides)
    return ImportState(**defaults)


def _step(index: int, *, status=StepStatus.PENDING, destructive=False) -> PlanStep:
    return PlanStep(
        index=index,
        kind=StepKind.TABLE,
        name=f"table-{index}",
        action=StepAction.CREATE,
        destructive=destructive,
        status=status,
    )


def test_touch_bumps_seq_and_updated_at():
    state = _import_state()
    assert state.seq == 0
    before = state.updated_at
    state.touch()
    state.touch()
    assert state.seq == 2
    assert state.updated_at >= before


def test_json_round_trip_preserves_document():
    state = _import_state()
    state.plan = ImportPlan(format_version=2, steps=[_step(0), _step(1, destructive=True)])
    state.status = ImportStatus.AWAITING_CONFIRMATION
    state.touch()

    restored = ImportState.model_validate(state.model_dump(mode="json"))

    assert restored == state


def test_next_pending_step_skips_done_and_resumes_running():
    plan = ImportPlan(
        format_version=2,
        steps=[
            _step(0, status=StepStatus.DONE),
            _step(1, status=StepStatus.RUNNING),
            _step(2),
        ],
    )
    # A RUNNING step means the worker died mid-step: it must be retried, not
    # skipped — apply-time upserts make the re-run safe.
    assert plan.next_pending_step().index == 1

    plan.steps[1].status = StepStatus.DONE
    assert plan.next_pending_step().index == 2

    plan.steps[2].status = StepStatus.SKIPPED
    assert plan.next_pending_step() is None


def test_has_destructive_steps_only_counts_pending():
    plan = ImportPlan(
        format_version=2,
        steps=[_step(0, destructive=True, status=StepStatus.DONE), _step(1)],
    )
    assert not plan.has_destructive_steps

    plan.steps[1].destructive = True
    assert plan.has_destructive_steps


def test_terminal_statuses():
    state = _import_state()
    assert not state.is_terminal
    state.status = ImportStatus.CANCELLED
    assert state.is_terminal

    export = ExportState(export_id=uuid4(), pod_id=uuid4(), user_id=uuid4())
    assert not export.is_terminal
    export.status = ExportStatus.READY
    assert export.is_terminal

    publish = PublishState(
        publish_id=uuid4(), pod_id=uuid4(), user_id=uuid4(), repo_name="crm"
    )
    assert not publish.is_terminal
    publish.status = PublishStatus.FAILED
    assert publish.is_terminal
