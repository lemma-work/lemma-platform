"""A pod's machines as definitions, for a module that copies a pod: pod bundles.

A bundle carries what a machine *is* -- its name, template, size, how many
runs it takes at once -- and nothing about its disk, its instance or who made
it. Who may use it travels as grants on the agents and functions that do. Creating one goes through the same policy check
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


async def pod_machine_id(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ref: str
) -> UUID | None:
    """The id of the machine of this pod a stored ref names, or None.

    For an agent's ``machine:`` -- the grant it confers is on this id. None
    for a ref that is not one of this pod's machines (``me``, ``mine:``,
    another pod's, a machine not created yet).
    """
    from app.modules.workspace.domain.machine import MachineRef, MachineRefKind
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )

    try:
        parsed = MachineRef.parse(ref)
    except ValueError:
        return None
    if parsed.kind is not MachineRefKind.POD or parsed.name is None:
        return None
    if parsed.pod is not None and parsed.pod != str(pod_id):
        return None
    machine = await MachineRepository(uow).pod_machine(pod_id, parsed.name)
    return machine.id if machine is not None else None


@dataclass(frozen=True, slots=True)
class BundledMachine:
    """What a bundle asks a machine to be, as the importer will ask for it."""

    name: str
    template: str
    size: str | None


async def pod_machine_refusals(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, machines: list[BundledMachine]
) -> list[str]:
    """Why :func:`upsert_pod_machine` would refuse each of these, read before
    anything is applied: the plan's policy on this deployment, which a
    bundle's literal template name cannot know -- `workspace-docker` is a
    valid template that only an E2B deployment can build.

    Checked as the upsert will act: an existing machine keeps its own
    template, so only a size it changes to is asked about. The person's
    editor role and the organization's machine count are the apply's to
    refuse; they say nothing about the bundle.
    """
    from app.modules.workspace.config import workspace_settings
    from app.modules.workspace.domain.machine import MachineSize, MachineTemplate
    from app.modules.workspace.domain.machine_errors import MachinePolicyRefused
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )
    from app.modules.workspace.services.pod_machines import (
        pod_policy,
        size_to_create,
    )

    if not machines:
        return []
    if not workspace_settings.pod_machines_enabled:
        return ["Pod machines are not available on this deployment."]
    policy = await pod_policy(uow, pod_id)
    repository = MachineRepository(uow)
    refusals: list[str] = []
    for wanted in machines:
        existing = await repository.pod_machine(pod_id, wanted.name)
        try:
            size = MachineSize.parse(wanted.size) if wanted.size else None
            if existing is None:
                template = MachineTemplate(wanted.template)
                policy.check(template, size_to_create(policy, template, size))
            elif size is not None and size != existing.size:
                policy.check(existing.template, size)
        except (ValueError, MachinePolicyRefused) as exc:
            refusals.append(f"Machine '{wanted.name}': {exc}")
    return refusals


async def upsert_pod_machine(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    template: str,
    size: str | None,
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
            run_slots=run_slots or 1,
        )
        return True
    await update_pod_machine(
        uow,
        existing,
        user_id=user_id,
        display_name=display_name,
        size=parsed_size,
        run_slots=run_slots,
    )
    return False


__all__ = [
    "BundledMachine",
    "PodMachineSpec",
    "pod_machine_refusals",
    "list_pod_machine_names",
    "list_pod_machines",
    "pod_machine_id",
    "upsert_pod_machine",
]
