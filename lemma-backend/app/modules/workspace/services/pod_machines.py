"""Pod machines: created by a pod's editors, shared by its agents, billed to its org.

A pod machine belongs to the pod, not to whoever made it: it survives its
creator leaving, any editor may change or delete it, and its compute counts
in the organization's window. Creating one is checked against the plan's
policy -- the template, the size, and how many the organization may have.

Who may use one beyond what its visibility allows is said with grants: the
same `resource_permission_grants` rows every pod resource uses, with
`machine.use` on resource type `machine`. Agents and functions by name,
people by membership.
"""

from __future__ import annotations

from sqlalchemy import Select

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, insert, select

from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine import (
    Machine,
    MachineSize,
    MachineTemplate,
    MachineVisibility,
    validate_machine_name,
)
from app.modules.workspace.domain.machine_access import PodStanding
from app.modules.workspace.domain.machine_errors import (
    MachineConflict,
    MachineNotFound,
    MachinePolicyRefused,
)
from app.modules.workspace.infrastructure.machine_facts import pod_standing
from app.modules.workspace.infrastructure.machine_repository import MachineRepository
from app.modules.workspace.services.machine_policy import (
    EffectivePolicy,
    policy_for_org,
)


@dataclass(frozen=True, slots=True)
class MachineGrants:
    agents: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    members: tuple[UUID, ...] = ()


async def require_pod_role(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, user_id: UUID, at_least: PodStanding
) -> PodStanding:
    standing = await pod_standing(uow, pod_id, user_id)
    if standing < at_least:
        if standing is PodStanding.NONE:
            raise MachineNotFound(f"pod:{pod_id}")
        raise MachinePolicyRefused(
            "Only the pod's editors can create or change its machines.",
            code="MACHINE_EDITOR_REQUIRED",
        )
    return standing


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
    visibility: MachineVisibility,
    run_slots: int,
) -> Machine:
    _enabled()
    await require_pod_role(uow, pod_id, user_id, PodStanding.EDITOR)
    try:
        validate_machine_name(name)
    except ValueError as exc:
        raise MachineConflict(str(exc), code="MACHINE_NAME_INVALID") from None
    policy = await pod_policy(uow, pod_id)
    chosen = size or (
        policy.default_size
        if template is MachineTemplate.WORKSPACE
        else policy.sizes_for(template)[0]
        if policy.sizes_for(template)
        else policy.default_size
    )
    policy.check(template, chosen)
    await _check_count(uow, pod_id, policy)
    repository = MachineRepository(uow)
    machine = await repository.create_pod_machine(
        pod_id=pod_id,
        name=name,
        display_name=display_name or name,
        template=template,
        size=chosen,
        visibility=visibility,
        run_slots=run_slots,
        created_by=user_id,
    )
    if machine is None:
        raise MachineConflict(
            f"This pod already has a machine called '{name}'.",
            code="MACHINE_NAME_TAKEN",
        )
    return machine


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
    visibility: MachineVisibility | None,
    run_slots: int | None,
) -> Machine:
    assert machine.pod_id is not None
    await require_pod_role(uow, machine.pod_id, user_id, PodStanding.EDITOR)
    values: dict[str, object] = {}
    if display_name is not None:
        values["display_name"] = display_name
    if size is not None and size != machine.size:
        (await pod_policy(uow, machine.pod_id)).check(machine.template, size)
        values["size"] = size.label
    if visibility is not None:
        values["visibility"] = visibility.value
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
    """Replace who is granted this machine. Unknown names are refused, all
    of them in one sentence."""
    assert machine.pod_id is not None
    from app.core.authorization.resource_names import resolve_resource_ids_by_names
    from app.modules.pod.contracts.members import pod_member_id

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
    unknown: list[str] = []
    for user_id in grants.members:
        member_id = await pod_member_id(uow, machine.pod_id, user_id)
        if member_id is None:
            unknown.append(str(user_id))
            continue
        rows.append(("POD_MEMBER", member_id))
    if unknown:
        raise MachineConflict(
            "Not members of this pod: " + ", ".join(unknown),
            code="MACHINE_GRANT_UNKNOWN_MEMBER",
        )
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
    from app.modules.pod.contracts.members import pod_member_user_id

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
    members: list[UUID] = []
    for member_id in by_type.get("POD_MEMBER", []):
        user_id = await pod_member_user_id(
            uow, pod_id=machine.pod_id, pod_member_id=member_id
        )
        if user_id is not None:
            members.append(user_id)
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
        members=tuple(sorted(members)),
    )
