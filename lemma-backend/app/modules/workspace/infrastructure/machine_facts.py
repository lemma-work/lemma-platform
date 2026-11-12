"""The facts the machine authorization matrix decides on, read from the database.

Each answer is one narrow query, asked only when the matrix needs it for this
caller and this machine: a person's own default machine never reads a pod
role, and a person's request never reads a workload grant.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import ResourceType
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import Machine, MachineKind
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineCaller,
    MachineFacts,
    PodStanding,
)
from app.modules.workspace.infrastructure.machine_models import (
    AppMachineConsentModel,
)

_WORKLOAD_GRANTEE = {CallerKind.AGENT: "AGENT", CallerKind.FUNCTION: "FUNCTION"}


class DatabaseMachineFacts:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def gather(self, caller: MachineCaller, machine: Machine) -> MachineFacts:
        if machine.kind is MachineKind.DEFAULT:
            return MachineFacts(
                app_consented=(
                    caller.kind is CallerKind.APP
                    and caller.user_id is not None
                    and caller.app_id is not None
                    and await self.app_consented(caller.user_id, caller.app_id)
                ),
                run_started_in_lemma=(
                    caller.kind is CallerKind.AGENT
                    and await self._run_started_in_lemma(caller)
                ),
            )
        pod_id = machine.pod_id
        if pod_id is None:
            return MachineFacts()
        if caller.kind in _WORKLOAD_GRANTEE:
            return MachineFacts(
                workload_granted=await self._workload_granted(caller, machine)
            )
        if caller.user_id is None:
            return MachineFacts()
        return MachineFacts(
            standing=await pod_standing(self._uow, pod_id, caller.user_id),
            member_granted=await self._member_granted(pod_id, caller.user_id, machine),
        )

    async def app_consented(self, user_id: UUID, app_id: UUID) -> bool:
        found = await self._uow.session.scalar(
            select(AppMachineConsentModel.id).where(
                AppMachineConsentModel.user_id == user_id,
                AppMachineConsentModel.app_id == app_id,
                AppMachineConsentModel.revoked_at.is_(None),
            )
        )
        return found is not None

    async def _run_started_in_lemma(self, caller: MachineCaller) -> bool:
        if caller.conversation_id is None or caller.user_id is None:
            return False
        from app.modules.agent.contracts.run_origin import (
            conversation_started_in_lemma,
        )

        return await conversation_started_in_lemma(
            self._uow,
            conversation_id=caller.conversation_id,
            user_id=caller.user_id,
        )

    async def _workload_granted(self, caller: MachineCaller, machine: Machine) -> bool:
        if caller.workload_id is None:
            return False
        return await self._granted(
            machine,
            grantee_type=_WORKLOAD_GRANTEE[caller.kind],
            grantee_id=caller.workload_id,
        )

    async def _member_granted(
        self, pod_id: UUID, user_id: UUID, machine: Machine
    ) -> bool:
        from app.modules.pod.contracts.members import pod_member_id

        member_id = await pod_member_id(self._uow, pod_id, user_id)
        if member_id is None:
            return False
        return await self._granted(
            machine, grantee_type="POD_MEMBER", grantee_id=member_id
        )

    async def _granted(
        self, machine: Machine, *, grantee_type: str, grantee_id: UUID
    ) -> bool:
        found = await self._uow.session.scalar(
            select(ResourcePermissionGrantModel.id).where(
                ResourcePermissionGrantModel.resource_type
                == ResourceType.MACHINE.value,
                ResourcePermissionGrantModel.resource_id == machine.id,
                ResourcePermissionGrantModel.grantee_type == grantee_type,
                ResourcePermissionGrantModel.grantee_id == grantee_id,
                ResourcePermissionGrantModel.permission_id == Permissions.MACHINE_USE,
            )
        )
        return found is not None


async def pod_standing(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, user_id: UUID
) -> PodStanding:
    from app.modules.pod.contracts.members import pod_member_holds

    if await pod_member_holds(uow, pod_id, user_id, Permissions.POD_MEMBER_MANAGE):
        return PodStanding.ADMIN
    if await pod_member_holds(uow, pod_id, user_id, Permissions.MACHINE_MANAGE):
        return PodStanding.EDITOR
    if await pod_member_holds(uow, pod_id, user_id, Permissions.POD_READ):
        return PodStanding.VIEWER
    return PodStanding.NONE
