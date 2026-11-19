"""A pod's machines in a bundle: exporting them, and planning their steps.

Split out of :mod:`exporter` and :mod:`plan_builder`, both at or past the size
the architecture ratchet allows to grow, on the same grounds as
:mod:`exporter_agents`. Applying one is :func:`agent_apply.apply_machine`.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Protocol
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from lemma_pod_bundle import load_resource_payload
from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import _normalize_machine_payload

from app.modules.pod_bundle.domain.errors import BundleInvalidError
from app.modules.pod_bundle.domain.state import PlanStep, StepKind
from app.modules.pod_bundle.infrastructure import kept_parts

if TYPE_CHECKING:
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.workspace.contracts.pod_machines import BundledMachine


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
    """The pod's machines, by name: what the plan diffs a bundle against --
    and why the target would refuse a bundled one."""

    async def machine_names(self) -> set[str]: ...

    async def machine_refusals(self, machines: list[BundledMachine]) -> list[str]: ...


async def machine_steps(bundle_root: Path, pod: MachineNames) -> list[PlanStep]:
    """One MACHINE step per bundled machine: CREATE, or kept when the pod
    already has one of that name (``kept_parts``: an import never replaces).

    The plan puts these before the agents that name a machine as theirs and
    before every grants step, since a `machine.use` grant names the machine.

    A machine the target cannot build -- a template its deployment does not
    offer (`workspace-docker` is E2B-only), a size its plan does not allow --
    fails the plan, naming each one. Found at apply, it failed partway, with
    every step before it already written.
    """
    type_dir = bundle_root / "machines"
    if not type_dir.is_dir():
        return []
    dirs = sorted((d for d in type_dir.iterdir() if d.is_dir()), key=lambda p: p.name)
    existing = await pod.machine_names()
    # Only a machine the import creates has to fit the target's plan; a kept
    # one is not built from the bundle at all.
    refusals = await pod.machine_refusals(
        [_bundled(d) for d in dirs if d.name not in existing]
    )
    if refusals:
        raise BundleInvalidError(
            "This pod cannot have the bundle's machines as they are: "
            + " ".join(refusals)
            + " Change them in the bundle, or import into a pod whose plan "
            "allows them.",
            details={"machines": refusals},
        )
    return [kept_parts.create_or_keep(StepKind.MACHINE, d.name, existing) for d in dirs]


def _bundled(machine_dir: Path) -> BundledMachine:
    """The machine as `apply_machine` will ask for it."""
    from app.modules.workspace.contracts.pod_machines import BundledMachine

    payload = load_resource_payload(
        machine_dir, machine_dir.name, resource_type="machines"
    )
    size = payload.get("size")
    return BundledMachine(
        name=machine_dir.name,
        template=str(payload.get("template") or "workspace"),
        size=str(size) if size else None,
    )


class PodMachineNames:
    """:class:`MachineNames` over a pod's UoW, for the production lookups."""

    _uow: SqlAlchemyUnitOfWork
    _pod_id: UUID

    async def machine_names(self) -> set[str]:
        from app.modules.workspace.contracts.pod_machines import (
            list_pod_machine_names,
        )

        return await list_pod_machine_names(self._uow, pod_id=self._pod_id)

    async def machine_refusals(self, machines: list[BundledMachine]) -> list[str]:
        from app.modules.workspace.contracts.pod_machines import (
            pod_machine_refusals,
        )

        return await pod_machine_refusals(
            self._uow, pod_id=self._pod_id, machines=machines
        )
