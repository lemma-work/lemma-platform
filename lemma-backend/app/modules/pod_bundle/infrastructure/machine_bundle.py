"""A pod's machines in a bundle: exporting them, and planning their steps.

Split out of :mod:`exporter` and :mod:`plan_builder`, both at or past the size
the architecture ratchet allows to grow, on the same grounds as
:mod:`exporter_agents`. Applying one is :func:`agent_apply.apply_machine`.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_machine_payload

from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind


async def export_machines(
    uow, root: Path, pod_id: UUID, *, selected: Collection[str]
) -> int:
    """One directory per pod machine: its definition, never its disk.

    Returns how many resource types it exported (0 or 1), for the progress
    count the exporter keeps.
    """
    if "machines" not in selected:
        return 0
    from app.modules.workspace.contracts.pod_machines import list_pod_machines

    for spec in await list_pod_machines(uow, pod_id=pod_id):
        dir_ = root / "machines" / spec.name
        dir_.mkdir(parents=True, exist_ok=True)
        _write_json(
            dir_ / f"{spec.name}.json", _normalize_machine_payload(asdict(spec))
        )
    return 1


class MachineNames(Protocol):
    """The pod's machines, by name: what the plan diffs a bundle against."""

    async def machine_names(self) -> set[str]: ...


async def machine_steps(bundle_root: Path, pod: MachineNames) -> list[PlanStep]:
    """One MACHINE step per bundled machine, UPDATE when the pod has it.

    The plan puts these before the agents that name a machine as theirs and
    before every grants step, since a `machine.use` grant names the machine.
    """
    type_dir = bundle_root / "machines"
    if not type_dir.is_dir():
        return []
    existing = await pod.machine_names()
    return [
        PlanStep(
            index=0,
            kind=StepKind.MACHINE,
            name=d.name,
            action=StepAction.UPDATE if d.name in existing else StepAction.CREATE,
        )
        for d in sorted(type_dir.iterdir(), key=lambda p: p.name)
        if d.is_dir()
    ]


async def pod_machine_names(uow, pod_id: UUID) -> set[str]:
    from app.modules.workspace.contracts.pod_machines import list_pod_machine_names

    return await list_pod_machine_names(uow, pod_id=pod_id)
