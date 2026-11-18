"""Refusing, at the plan, a bundle whose second schedule on a workflow would fail.

The rule and its reasons are ``lemma_pod_bundle.workflow_schedules``'. This is
the server's call of it: a workflow manifest with no ``mode`` is created USER
by this import (``applier``), and what the pod already has -- a workflow, or a
schedule of the same name -- is kept rather than created, so it is not counted.
Asked once the plan's steps are made, so a schedule the plan skips (it names
an account the importer cannot use) is not counted either.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from lemma_pod_bundle import schedules_sharing_a_global_workflow

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind


def refuse_shared_workflows(
    bundle_root: Path,
    steps: Sequence[PlanStep],
    existing_schedules: Collection[str],
    existing_workflows: Collection[str],
) -> None:
    """Raise ``BundleInvalidError`` before anything is written when two of the
    bundle's new schedules would start one new GLOBAL workflow."""
    clash = schedules_sharing_a_global_workflow(
        bundle_root,
        default_mode="USER",
        existing_schedules=existing_schedules,
        existing_workflows=existing_workflows,
        left_out=[
            step.name
            for step in steps
            if step.kind is StepKind.SCHEDULE and step.action is StepAction.SKIP
        ],
    )
    if clash is not None:
        raise BundleInvalidError(clash)


__all__ = ["refuse_shared_workflows"]
