"""A pod's machines as definitions, for a module that copies a pod: pod bundles.

A bundle carries what a machine *is* -- its name, template, size, who in the
pod sees it, how many runs it takes at once -- and nothing about its disk,
its instance or who made it. Creating one goes through the same policy check
the `/pods/{pod_id}/machines` route runs, as the person importing.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class PodMachineSpec:
    """One pod machine, in the words a bundle uses."""

    name: str
    display_name: str
    template: str
    size: str
    visibility: str
    run_slots: int


async def list_pod_machines(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> list[PodMachineSpec]:
    """The pod's live machines, by name."""
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )

    machines = await MachineRepository(uow).list_for_pod(pod_id)
    return sorted(
        (
            PodMachineSpec(
                name=machine.name,
                display_name=machine.display_name,
                template=machine.template.value,
                size=machine.size.label,
                visibility=machine.visibility.value,
                run_slots=machine.run_slots,
            )
            for machine in machines
        ),
        key=lambda spec: spec.name,
    )


async def list_pod_machine_names(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> set[str]:
    return {spec.name for spec in await list_pod_machines(uow, pod_id=pod_id)}


async def upsert_pod_machine(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    template: str,
    size: str | None,
    visibility: str,
    run_slots: int | None,
    display_name: str | None,
) -> bool:
    """Create the pod machine called ``name``, or bring an existing one to this
    definition. True when it was created.

    A machine's template is what its disk was built from, so an existing
    machine keeps its own: changing it would mean a new machine, and an import
    never deletes one. Everything else follows the bundle. Refusals are the
    machine module's own -- an editor is required, the plan must offer the
    template and size, the organization must have room for another.
    """
    from app.modules.workspace.domain.machine import (
        MachineSize,
        MachineTemplate,
        MachineVisibility,
    )
    from app.modules.workspace.domain.machine_errors import MachinePolicyRefused
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )
    from app.modules.workspace.services.pod_machines import (
        create_pod_machine,
        update_pod_machine,
    )

    try:
        parsed_size = MachineSize.parse(size) if size else None
    except ValueError as exc:
        raise MachinePolicyRefused(str(exc), code="MACHINE_SIZE_NOT_OFFERED") from None
    existing = await MachineRepository(uow).pod_machine(pod_id, name)
    if existing is None:
        await create_pod_machine(
            uow,
            pod_id=pod_id,
            user_id=user_id,
            name=name,
            display_name=display_name,
            template=MachineTemplate(template),
            size=parsed_size,
            visibility=MachineVisibility(visibility),
            run_slots=run_slots or 1,
        )
        return True
    await update_pod_machine(
        uow,
        existing,
        user_id=user_id,
        display_name=display_name,
        size=parsed_size,
        visibility=MachineVisibility(visibility),
        run_slots=run_slots,
    )
    return False


__all__ = [
    "PodMachineSpec",
    "list_pod_machine_names",
    "list_pod_machines",
    "upsert_pod_machine",
]
