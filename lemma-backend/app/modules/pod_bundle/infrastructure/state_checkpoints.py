"""A bundle job's checkpoints, and the transitions its state may make.

* ``bundle_checkpoints``: the per-step rows the state is mirrored into
  (``pod_bundle_job_steps``) -- each apply step, the export archive, the
  repository and each file of a publish -- so a resumed job and an operator
  read the same checkpoints.
* ``validate_transition``: what the store refuses to write over the current
  row (a finished job, a won cancellation, a retry that skips an attempt).

Pure functions of the states; the store writes them.
"""

from __future__ import annotations

from app.modules.pod_bundle.domain.errors import BundleStateConflictError
from app.modules.pod_bundle.domain.state import (
    BundleJobKind,
    ExportState,
    ExportStatus,
    ImportState,
    ImportStatus,
    PublishState,
    PublishStatus,
    StepStatus,
)

type BundleState = ImportState | ExportState | PublishState

_IMMUTABLE_STATUSES = {
    BundleJobKind.IMPORT: {
        ImportStatus.COMPLETED.value,
        ImportStatus.CANCELLED.value,
        ImportStatus.PARTIALLY_CANCELLED.value,
    },
    BundleJobKind.EXPORT: {ExportStatus.READY.value},
    BundleJobKind.PUBLISH: {PublishStatus.COMPLETED.value},
}


def _import_checkpoints(state: ImportState) -> list[dict[str, object]]:
    if state.plan is None:
        return []
    return [
        {
            "step_index": step.index,
            "phase": "APPLY",
            "kind": step.kind.value,
            "name": step.name,
            "status": step.status.value,
            "error": step.error,
            "committed_at": (
                state.updated_at
                if step.status in {StepStatus.DONE, StepStatus.SKIPPED}
                else None
            ),
        }
        for step in state.plan.steps
    ]


def _export_checkpoints(state: ExportState) -> list[dict[str, object]]:
    status = {
        ExportStatus.QUEUED: StepStatus.PENDING,
        ExportStatus.EXPORTING: StepStatus.RUNNING,
        ExportStatus.READY: StepStatus.DONE,
        ExportStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    return [
        {
            "step_index": 0,
            "phase": "EXPORT",
            "kind": "ARCHIVE",
            "name": "Export pod bundle",
            "status": status.value,
            "error": state.error,
            "committed_at": (state.completed_at if status is StepStatus.DONE else None),
        }
    ]


def _publish_checkpoints(state: PublishState) -> list[dict[str, object]]:
    export_status = {
        PublishStatus.QUEUED: StepStatus.PENDING,
        PublishStatus.EXPORTING: StepStatus.RUNNING,
        PublishStatus.PUBLISHING: StepStatus.DONE,
        PublishStatus.COMPLETED: StepStatus.DONE,
        PublishStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    publish_status = {
        PublishStatus.QUEUED: StepStatus.PENDING,
        PublishStatus.EXPORTING: StepStatus.PENDING,
        PublishStatus.PUBLISHING: StepStatus.RUNNING,
        PublishStatus.COMPLETED: StepStatus.DONE,
        PublishStatus.FAILED: StepStatus.FAILED,
    }[state.status]
    checkpoints: list[dict[str, object]] = [
        {
            "step_index": 0,
            "phase": "EXPORT",
            "kind": "ARCHIVE",
            "name": "Export pod bundle",
            "status": export_status.value,
            "error": state.error if export_status is StepStatus.FAILED else None,
            "committed_at": (
                state.updated_at if export_status is StepStatus.DONE else None
            ),
        },
        {
            "step_index": 1,
            "phase": "PUBLISH",
            "kind": "REPOSITORY",
            "name": state.repo_name,
            "status": publish_status.value,
            "error": state.error if publish_status is StepStatus.FAILED else None,
            "committed_at": (
                state.completed_at if publish_status is StepStatus.DONE else None
            ),
        },
    ]
    checkpoints.extend(
        {
            "step_index": index + 2,
            "phase": "PUBLISH",
            "kind": "FILE",
            "name": file.path,
            "status": file.status.value,
            "error": file.error,
            "committed_at": (
                state.updated_at if file.status is StepStatus.DONE else None
            ),
        }
        for index, file in enumerate(state.files)
    )
    return checkpoints


def bundle_checkpoints(state: BundleState) -> list[dict[str, object]]:
    if isinstance(state, ImportState):
        return _import_checkpoints(state)
    if isinstance(state, ExportState):
        return _export_checkpoints(state)
    return _publish_checkpoints(state)


def validate_transition(
    kind: BundleJobKind,
    current: BundleState,
    candidate: BundleState,
    *,
    allow_failed_reopen: bool,
) -> None:
    old_status = current.status.value
    new_status = candidate.status.value
    if old_status in _IMMUTABLE_STATUSES[kind]:
        raise BundleStateConflictError("A completed pod bundle job is immutable.")
    if old_status == "FAILED" and new_status != "FAILED":
        if not allow_failed_reopen or candidate.attempt != current.attempt + 1:
            raise BundleStateConflictError(
                "A failed pod bundle job requires an explicit retry."
            )
    elif current.retryable and not candidate.retryable:
        if candidate.attempt != current.attempt + 1:
            raise BundleStateConflictError("A retry must advance exactly once.")
    elif candidate.attempt != current.attempt:
        raise BundleStateConflictError("A job attempt can change only on retry.")
    if old_status == ImportStatus.CANCELLING.value and new_status not in {
        ImportStatus.CANCELLING.value,
        ImportStatus.CANCELLED.value,
        ImportStatus.PARTIALLY_CANCELLED.value,
    }:
        raise BundleStateConflictError("Cancellation already won this job.")
    if (
        isinstance(current, ImportState)
        and isinstance(candidate, ImportState)
        and not set(current.committed_steps).issubset(candidate.committed_steps)
    ):
        raise BundleStateConflictError("Committed import steps cannot be removed.")


__all__ = ["bundle_checkpoints", "validate_transition"]
