"""Pod machines: created by a pod's editors, shared by its agents, billed to its org.

A pod machine belongs to the pod, not to whoever made it: it survives its
creator leaving, any editor may change or delete it, and its compute counts
in the organization's window. Creating one is checked against the plan's
policy -- the template, the size, and how many the organization may have.

It is a pod resource like any other: members use and manage it by their pod
role (`machine.use`, `machine.manage`), and agents and functions by a grant --
the same `resource_permission_grants` rows every pod resource uses, with
`machine.use` on resource type `machine` -- intersected with the person they
act for. Keeping one from somebody is not granting it to them.
"""

from __future__ import annotations

from sqlalchemy import Select

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, insert, select

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import (
    Machine,
    MachineSize,
    MachineTemplate,
    validate_machine_name,
)
from app.modules.workspace.domain.machine_access import CallerKind, MachineCaller
from app.modules.workspace.domain.machine_errors import (
    MachineConflict,
    MachineNotFound,
    MachinePolicyRefused,
)
from app.modules.workspace.infrastructure.machine_facts import DatabaseMachineFacts
from app.modules.workspace.infrastructure.machine_repository import MachineRepository
from app.modules.workspace.services.machine_policy import (
    EffectivePolicy,
    policy_for_org,
)


@dataclass(frozen=True, slots=True)
class MachineGrants:
    agents: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()


async def require_in_pod(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, user_id: UUID, *, manage: bool
) -> None:
    """The person may read the pod -- and, with ``manage``, change its
    machines (``machine.manage``, an editor's). A pod they cannot read is
    reported as one that does not exist."""
    ctx = await DatabaseMachineFacts(uow).context(
        MachineCaller(CallerKind.USER, user_id=user_id), pod_id
    )
    pod = ResourceRef.pod(pod_id)
    if ctx is None or not await ctx.can(Permissions.POD_READ, pod):
        raise MachineNotFound(f"pod:{pod_id}")
    if manage and not await ctx.can(Permissions.MACHINE_MANAGE, pod):
        raise MachinePolicyRefused(
            "Only the pod's editors can create or change its machines.",
            code="MACHINE_EDITOR_REQUIRED",
        )


async def pod_policy(uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> EffectivePolicy:
    from app.modules.pod.contracts.members import pod_organization_id

    return await policy_for_org(uow, await pod_organization_id(uow, pod_id))


def _enabled() -> None:
    if not workspace_settings.pod_machines_enabled:
        raise MachinePolicyRefused(
            "Pod machines are not available on this deployment.",
            code="POD_MACHINES_DISABLED",
        )


async def create_pod_machine(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    name: str,
    display_name: str | None,
    template: MachineTemplate,
    size: MachineSize | None,
    run_slots: int,
) -> Machine:
    _enabled()
    await require_in_pod(uow, pod_id, user_id, manage=True)
    try:
        validate_machine_name(name)
    except ValueError as exc:
        raise MachineConflict(str(exc), code="MACHINE_NAME_INVALID") from None
    policy = await pod_policy(uow, pod_id)
    chosen = size_to_create(policy, template, size)
    policy.check(template, chosen)
    await _check_count(uow, pod_id, policy)
    repository = MachineRepository(uow)
    machine = await repository.create_pod_machine(
        pod_id=pod_id,
        name=name,
        display_name=display_name or name,
        template=template,
        size=chosen,
        run_slots=run_slots,
        created_by=user_id,
    )
    if machine is None:
        raise MachineConflict(
            f"This pod already has a machine called '{name}'.",
            code="MACHINE_NAME_TAKEN",
        )
    return machine


def size_to_create(
    policy: EffectivePolicy, template: MachineTemplate, size: MachineSize | None
) -> MachineSize:
    """The size a new machine gets: the one asked for, else the plan's default
    for a plain workspace, else the template's smallest the plan offers."""
    if size is not None:
        return size
    offered = policy.sizes_for(template)
    if template is MachineTemplate.WORKSPACE or not offered:
        return policy.default_size
    return offered[0]


def bundle_refusals(
    policy: EffectivePolicy,
    machines: list[tuple[str, str, str | None]],
    existing: dict[str, Machine],
) -> list[str]:
    """Why creating or updating each ``(name, template, size)`` would be
    refused under ``policy``, as the upsert will act: a new machine gets
    :func:`size_to_create`; an existing one keeps its own template, so only a
    size it changes to is asked about."""
    refusals: list[str] = []
    for name, template_name, size_label in machines:
        found = existing.get(name)
        try:
            size = MachineSize.parse(size_label) if size_label else None
            if found is None:
                template = MachineTemplate(template_name)
                policy.check(template, size_to_create(policy, template, size))
            elif size is not None and size != found.size:
                policy.check(found.template, size)
        except (ValueError, MachinePolicyRefused) as exc:
            refusals.append(f"Machine '{name}': {exc}")
    return refusals


async def _check_count(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, policy: EffectivePolicy
) -> None:
    if policy.max_pod_machines is None:
        return
    from app.modules.pod.contracts.members import pod_organization_id

    organization_id = await pod_organization_id(uow, pod_id)
    pod_ids = _org_pods(organization_id) if organization_id else [pod_id]
    count = await MachineRepository(uow).count_pod_machines(pod_ids)
    if count >= policy.max_pod_machines:
        raise MachinePolicyRefused(
            f"Your plan allows {policy.max_pod_machines} pod machines; "
            "delete one or change plan to add another.",
            code="MACHINE_LIMIT_REACHED",
        )


def _org_pods(organization_id: UUID) -> Select[tuple[UUID]]:
    from app.modules.pod.contracts.provisioning import organization_pod_ids_query

    return organization_pod_ids_query(organization_id)


async def update_pod_machine(
    uow: SqlAlchemyUnitOfWork,
    machine: Machine,
    *,
    user_id: UUID,
    display_name: str | None,
    size: MachineSize | None,
    run_slots: int | None,
) -> Machine:
    assert machine.pod_id is not None
    await require_in_pod(uow, machine.pod_id, user_id, manage=True)
    values: dict[str, object] = {}
    if display_name is not None:
        values["display_name"] = display_name
    if size is not None and size != machine.size:
        (await pod_policy(uow, machine.pod_id)).check(machine.template, size)
        values["size"] = size.label
    if run_slots is not None:
        values["run_slots"] = run_slots
    repository = MachineRepository(uow)
    await repository.update_machine(machine.id, **values)
    updated = await repository.get(machine.id)
    assert updated is not None
    return updated


async def set_grants(
    uow: SqlAlchemyUnitOfWork,
    machine: Machine,
    grants: MachineGrants,
    *,
    created_by: UUID,
) -> MachineGrants:
    """Replace which agents and functions are granted this machine. Unknown
    names are refused, all of them in one sentence. People need no grant:
    their pod role is what lets them use a pod machine."""
    assert machine.pod_id is not None
    from app.core.authorization.resource_names import resolve_resource_ids_by_names

    refs = [(ResourceType.AGENT, name) for name in grants.agents] + [
        (ResourceType.FUNCTION, name) for name in grants.functions
    ]
    resolved = (
        await resolve_resource_ids_by_names(
            uow.session, pod_id=machine.pod_id, refs=refs
        )
        if refs
        else {}
    )
    rows: list[tuple[str, UUID]] = [
        ("AGENT", resolved[(ResourceType.AGENT, name)]) for name in grants.agents
    ] + [
        ("FUNCTION", resolved[(ResourceType.FUNCTION, name)])
        for name in grants.functions
    ]
    await uow.session.execute(
        delete(ResourcePermissionGrantModel).where(
            ResourcePermissionGrantModel.resource_type == ResourceType.MACHINE.value,
            ResourcePermissionGrantModel.resource_id == machine.id,
        )
    )
    if rows:
        await uow.session.execute(
            insert(ResourcePermissionGrantModel),
            [
                {
                    "pod_id": machine.pod_id,
                    "resource_type": ResourceType.MACHINE.value,
                    "resource_id": machine.id,
                    "grantee_type": grantee_type,
                    "grantee_id": grantee_id,
                    "permission_id": Permissions.MACHINE_USE,
                    "created_by_user_id": created_by,
                }
                for grantee_type, grantee_id in set(rows)
            ],
        )
    return await read_grants(uow, machine)


async def read_grants(uow: SqlAlchemyUnitOfWork, machine: Machine) -> MachineGrants:
    assert machine.pod_id is not None
    from app.core.authorization.resource_names import resolve_resource_names_by_ids

    rows = (
        await uow.session.execute(
            select(
                ResourcePermissionGrantModel.grantee_type,
                ResourcePermissionGrantModel.grantee_id,
            ).where(
                ResourcePermissionGrantModel.resource_type
                == ResourceType.MACHINE.value,
                ResourcePermissionGrantModel.resource_id == machine.id,
                ResourcePermissionGrantModel.permission_id == Permissions.MACHINE_USE,
            )
        )
    ).all()
    by_type: dict[str, list[UUID]] = {}
    for grantee_type, grantee_id in rows:
        by_type.setdefault(grantee_type, []).append(grantee_id)
    names = await resolve_resource_names_by_ids(
        uow.session,
        pod_id=machine.pod_id,
        refs=[(ResourceType.AGENT, i) for i in by_type.get("AGENT", [])]
        + [(ResourceType.FUNCTION, i) for i in by_type.get("FUNCTION", [])],
    )
    return MachineGrants(
        agents=tuple(
            sorted(
                n
                for i in by_type.get("AGENT", [])
                if (n := names.get((ResourceType.AGENT, i)))
            )
        ),
        functions=tuple(
            sorted(
                n
                for i in by_type.get("FUNCTION", [])
                if (n := names.get((ResourceType.FUNCTION, i)))
            )
        ),
    )
