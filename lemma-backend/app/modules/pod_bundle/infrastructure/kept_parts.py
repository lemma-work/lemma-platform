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

from collections.abc import Collection, Mapping, Sequence
from typing import Protocol

from lemma_pod_bundle import diff_table_columns

from app.modules.pod_bundle.domain.errors import PartKeptError
from app.modules.pod_bundle.domain.state import (
    PlanStep,
    StepAction,
    StepKind,
    StepStatus,
    part_key,
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
ROWS_LOADED = "its rows were loaded by an earlier attempt of this import"
ACCESS_KEPT = (
    "it was already here, not created by this import, so its access is kept as it is"
)


class CreatedParts:
    """What this import created, read and written by the steps that apply it.

    Wraps the import's live ``ImportState.created``, which is checkpointed
    with every step, the way the warnings list is. A step that creates a part
    records it here; a step that finds its part already there asks here
    whether it is the import's own (:meth:`claim`); a grants or seed step
    writes only to a part recorded here (:meth:`require`).
    """

    def __init__(self, keys: list[str] | None = None):
        self._keys = keys if keys is not None else []

    def made(self, kind: StepKind, name: str) -> bool:
        return part_key(kind, name) in self._keys

    def add(self, kind: StepKind, name: str) -> None:
        if not self.made(kind, name):
            self._keys.append(part_key(kind, name))

    def claim(self, step: PlanStep, *, resumed: bool) -> None:
        """For a step that finds its part already there: return when it is
        this import's own, refuse with :class:`PartKeptError` when not.

        Its own when an earlier attempt recorded it, or when this step was
        interrupted while running: its write committed and the worker stopped
        before its checkpoint did. Otherwise someone else made it, between
        the plan and now, or before a plan made earlier said otherwise."""
        if self.made(step.kind, step.name):
            return
        if resumed:
            self.add(step.kind, step.name)
            return
        raise PartKeptError(kept_step(step.kind, step.name).error or "")

    def require(self, kind: StepKind, name: str, reason: str) -> None:
        """Refuse a write to a part this import did not create."""
        if not self.made(kind, name):
            raise PartKeptError(reason)


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


class ExistingResources(Protocol):
    """The pod's current resources, by name — everything the plan's diff needs."""

    async def table_names(self) -> set[str]: ...
    async def table_manifest(self, name: str) -> dict[str, object] | None: ...
    async def function_names(self) -> set[str]: ...
    async def agent_names(self) -> set[str]: ...
    async def workflow_names(self) -> set[str]: ...
    async def schedule_names(self) -> set[str]: ...
    async def app_names(self) -> set[str]: ...
    async def surface_names(self) -> set[str]: ...
    async def existing_paths(self, paths: Sequence[str]) -> set[str]:
        """Which of these file and folder paths the pod already holds."""
        ...


class TheirParts:
    """The pod's parts as one import plans against them: less its own.

    A replan builds a fresh plan against a pod already holding what the
    import's earlier attempt made. Counted as missing, those are planned CREATE
    again, so the plan still carries their grants and rows, and their steps
    find them as the import's own and finish -- an app left half built is
    built. A first plan has made nothing, and sees the pod as it is.
    """

    def __init__(self, existing: ExistingResources, created: Collection[str] = ()):
        self._existing = existing
        self._created = frozenset(created)

    def ours(self, kind: StepKind, name: str) -> bool:
        return part_key(kind, name) in self._created

    def _less_ours(self, kind: StepKind, names: set[str]) -> set[str]:
        return {name for name in names if not self.ours(kind, name)}

    async def table_names(self) -> set[str]:
        return self._less_ours(StepKind.TABLE, await self._existing.table_names())

    async def table_manifest(self, name: str) -> dict[str, object] | None:
        return await self._existing.table_manifest(name)

    async def function_names(self) -> set[str]:
        return self._less_ours(StepKind.FUNCTION, await self._existing.function_names())

    async def agent_names(self) -> set[str]:
        return self._less_ours(StepKind.AGENT, await self._existing.agent_names())

    async def workflow_names(self) -> set[str]:
        return self._less_ours(StepKind.WORKFLOW, await self._existing.workflow_names())

    async def schedule_names(self) -> set[str]:
        return self._less_ours(StepKind.SCHEDULE, await self._existing.schedule_names())

    async def app_names(self) -> set[str]:
        return self._less_ours(StepKind.APP, await self._existing.app_names())

    async def surface_names(self) -> set[str]:
        """By the name a surface is created under, which is not the name its
        step carries: the plan asks :meth:`ours` of each surface step."""
        return await self._existing.surface_names()

    async def existing_paths(self, paths: Sequence[str]) -> set[str]:
        return await self._existing.existing_paths(paths)


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


def table_data_step(
    name: str, *, table_created: bool, loaded: bool = False
) -> PlanStep:
    """Seed rows go only into a table this import creates.

    A table that was already here holds the pod's own rows, or an earlier
    install's. Rows without an id cannot be matched to those, so loading them
    again adds them twice; rows with an id are upserted, which writes the
    bundle's values over a row someone has since edited. Either way the table
    would change under the person, so its rows are left exactly as they are.
    A replan does not load them twice into a table the import made (``loaded``).
    """
    if table_created and not loaded:
        return PlanStep(
            index=0, kind=StepKind.TABLE_DATA, name=name, action=StepAction.CREATE
        )
    step = kept_step(StepKind.TABLE_DATA, name)
    step.error = ROWS_LOADED if loaded else ROWS_KEPT
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
    "ACCESS_KEPT",
    "ROWS_KEPT",
    "ROWS_LOADED",
    "CreatedParts",
    "ExistingResources",
    "TheirParts",
    "create_or_keep",
    "existing_table_step",
    "grants_steps",
    "keep_existing_files",
    "kept_step",
    "table_data_step",
]
