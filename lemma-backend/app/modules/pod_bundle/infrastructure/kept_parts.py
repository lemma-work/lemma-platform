"""What an import does with a part the pod already has: it keeps it.

An import adds to a pod and never takes away from it. Since a part arrived, a
person may have edited the app, added a column or rewritten the agent's
instructions, and installing another bundle -- a plugin that carries the base
it builds on, say -- must not undo any of that. So every part the bundle names
that the pod already has is planned SKIP with the reason on its step, and the
apply loop never runs it. A table is the one part that still changes, and only
by gaining the columns it lacks.

There is no mode that replaces. A builder redeploying their own pod from a
local bundle uses lemma-cli's direct import (``lemma pods import``, which
upserts by default): it writes through the API and never reaches this plan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from lemma_pod_bundle import diff_table_columns

from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
)

#: What else stays with each kind of part, said on its step.
_KEPT_WITH: Mapping[StepKind, str] = {
    StepKind.TABLE: ", with its columns and rows",
    StepKind.FUNCTION: ", with its code and access",
    StepKind.AGENT: ", with its instructions and access",
    StepKind.SURFACE: ", with its settings",
    StepKind.APP: ", with the version it serves and who can open it",
}

ROWS_KEPT = (
    "the table was already here, so its rows are kept as they are and the "
    "bundle's rows are not added"
)


def kept_step(kind: StepKind, name: str) -> PlanStep:
    """A step for a part the pod already has: planned SKIP and marked SKIPPED.

    SKIPPED as well as SKIP is what keeps the apply loop from running it:
    ``next_pending_step`` hands back only PENDING and RUNNING steps, and
    progress counts a SKIPPED step as accounted for. Planning UPDATE for a part
    the applier then leaves alone would report a change that never happened.
    """
    return PlanStep(
        index=0,
        kind=kind,
        name=name,
        action=StepAction.SKIP,
        status=StepStatus.SKIPPED,
        error=f"already exists here; kept as it is{_KEPT_WITH.get(kind, '')}",
    )


def create_or_keep(kind: StepKind, name: str, existing: set[str]) -> PlanStep:
    """CREATE for a part the pod lacks, kept for one it has."""
    if name in existing:
        return kept_step(kind, name)
    return PlanStep(index=0, kind=kind, name=name, action=StepAction.CREATE)


def existing_table_step(
    name: str,
    current: dict[str, object] | None,
    desired: dict[str, object],
) -> tuple[PlanStep, list[str]]:
    """The step for a table the pod already has, and what the plan should warn.

    It gains the bundle's columns it lacks, and nothing else: a column the
    bundle does not mention is the pod's own, and one the bundle defines
    differently stays as the pod has it. That difference is a warning, since
    what the bundle brings may expect its own definition."""
    if current is None:
        # Listed a moment ago and gone now: the applier creates it.
        return PlanStep(
            index=0, kind=StepKind.TABLE, name=name, action=StepAction.CREATE
        ), []
    diff = diff_table_columns(current, desired)
    adds = [str(column.get("name")) for column in diff.to_add]
    detail: dict[str, object] = {}
    warnings: list[str] = []
    if diff.incompatible:
        detail["columns_kept"] = list(diff.incompatible)
        warnings.append(
            f"Table '{name}' already has {', '.join(diff.incompatible)}, defined "
            "differently from the bundle; kept as they are."
        )
    if adds:
        detail["columns_to_add"] = adds
        step = PlanStep(
            index=0, kind=StepKind.TABLE, name=name, action=StepAction.UPDATE
        )
    else:
        step = kept_step(StepKind.TABLE, name)
    step.detail = detail
    return step, warnings


def table_data_step(name: str, *, table_created: bool) -> PlanStep:
    """Seed rows go only into a table this import creates.

    A table that was already here holds the pod's own rows, or an earlier
    install's. Rows without an id cannot be matched to those, so loading them
    again adds them twice; rows with an id are upserted, which writes the
    bundle's values over a row someone has since edited. Either way the table
    would change under the person, so its rows are left exactly as they are.
    """
    if table_created:
        return PlanStep(
            index=0, kind=StepKind.TABLE_DATA, name=name, action=StepAction.CREATE
        )
    step = kept_step(StepKind.TABLE_DATA, name)
    step.error = ROWS_KEPT
    return step


def keep_existing_files(
    steps: Sequence[PlanStep], existing_paths: set[str]
) -> list[PlanStep]:
    """FILE steps, with each path the pod already has kept.

    A folder that is there keeps its description and visibility, a file its
    bytes; what the bundle adds inside a kept folder is still created."""
    planned: list[PlanStep] = []
    for step in steps:
        if "/" + step.name not in existing_paths:
            planned.append(step)
            continue
        kept = kept_step(StepKind.FILE, step.name)
        kept.detail = dict(step.detail)
        planned.append(kept)
    return planned


def grants_steps(
    kind: StepKind, holders: Sequence[str], created: set[str]
) -> list[PlanStep]:
    """A deferred grants step for each holder this import creates.

    A function or agent the pod already had keeps the access it has: its
    grants are part of it, and the bundle's would replace them whole."""
    return [
        PlanStep(index=0, kind=kind, name=name, action=StepAction.CREATE)
        for name in holders
        if name in created
    ]


__all__ = [
    "ROWS_KEPT",
    "create_or_keep",
    "existing_table_step",
    "grants_steps",
    "keep_existing_files",
    "kept_step",
    "table_data_step",
]
