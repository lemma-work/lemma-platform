"""What a machine decision reads: the core authorizer, and whom a run answers.

A pod machine is a pod resource, so whether a caller may see, use or manage
one is asked of the core authorizer with the caller's own context -- built
the way every request builds it: a person's for a person and an app's
viewer, the delegated workload's (grant intersected with its person) for an
agent or a function, the person's own for the pod's default agent, the
function's grants alone for a function run with nobody behind it.

The ref handed to the authorizer is already hydrated from the machine row
the resolver read, so the core needs nothing of this module's tables.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import (
    Context,
    ResourceRef,
    ResourceType,
    ResourceVisibility,
)
from app.core.authorization.outside_text_gate import outside_text_hold
from app.core.authorization.delegation import is_pod_default_agent
from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.authorization.session_approvals import approve_for_this_context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import Machine
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineCaller,
    PodAccess,
)


def machine_resource(machine: Machine, pod_id: UUID) -> ResourceRef:
    """The machine as the authorizer sees it: a pod resource every member's
    role reaches, with no owner -- a pod machine is the pod's, not whoever
    made it."""
    return ResourceRef(
        resource_type=ResourceType.MACHINE,
        resource_id=machine.id,
        pod_id=pod_id,
        organization_id=machine.organization_id,
        visibility=ResourceVisibility.POD,
    )


class DatabaseMachineFacts:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._uow = uow

    async def answers_member(self, caller: MachineCaller) -> bool:
        """Whether the agent run's conversation answers a member, not a contact
        or another pod. No conversation reads as no: fail closed."""
        if caller.conversation_id is None:
            return False
        from app.modules.agent.contracts.run_audience import (
            conversation_answers_member,
        )

        return await conversation_answers_member(
            self._uow, conversation_id=caller.conversation_id
        )

    async def pod_access(self, caller: MachineCaller, machine: Machine) -> PodAccess:
        pod_id = machine.pod_id
        if pod_id is None:
            return PodAccess()
        ctx = await self.context(caller, pod_id)
        if ctx is None:
            return PodAccess()
        resource = machine_resource(machine, pod_id)
        manage = await ctx.can(Permissions.MACHINE_MANAGE, resource)
        use = manage or await ctx.can(Permissions.MACHINE_USE, resource)
        # Held, not refused: a run reading strangers' words that its person
        # can let through. It may know the machine is there -- it is asking.
        held = not use and await _held(ctx, resource)
        # Seeing a machine is reading the pod it is in -- for people. A
        # workload sees what it may use and nothing else.
        see = (
            use
            or held
            or (
                caller.kind in (CallerKind.USER, CallerKind.APP)
                and await ctx.can(Permissions.POD_READ, ResourceRef.pod(pod_id))
            )
        )
        return PodAccess(manage=manage, use=use, see=see, held=held)

    async def held_for_outside_text(self, caller: MachineCaller) -> bool:
        """Whether this agent run is reading what people outside the pod wrote
        and its person has not approved its using a machine.

        For a person's own machine, which the authorizer does not decide: the
        same gate it applies to a pod machine, asked of the run's own context
        in its pod. One read of the mark first, so a run with nothing from
        outside in front of it pays nothing more.
        """
        from app.core.authorization.outside_text import has_read_outside_text

        if caller.kind is not CallerKind.AGENT or caller.conversation_id is None:
            return False
        if not await has_read_outside_text(str(caller.conversation_id)):
            return False
        if caller.pod_id is None:
            return True
        ctx = await self.context(caller, caller.pod_id)
        # No context in its pod is no person to approve: held.
        return ctx is None or await _held(ctx, None)

    async def pods_named(self, caller: MachineCaller, name: str) -> list[UUID]:
        """The pods ``pod:<name>:...`` could mean to this caller: a person's
        by their memberships, a workload's or an app's only its own pod."""
        if caller.kind is CallerKind.USER and caller.user_id is not None:
            from app.modules.pod.contracts.members import member_pods_named

            return await member_pods_named(self._uow, user_id=caller.user_id, name=name)
        if caller.pod_id is None:
            return []
        from app.modules.pod.contracts.members import pod_name

        own = await pod_name(self._uow.session, caller.pod_id)
        if own is not None and own.strip().lower() == name.strip().lower():
            return [caller.pod_id]
        return []

    async def context(self, caller: MachineCaller, pod_id: UUID) -> Context | None:
        """The caller's authorization context in ``pod_id``, or None when it
        has none there at all."""
        service = create_authorization_data_service(self._uow)
        if caller.kind in (CallerKind.USER, CallerKind.APP):
            if caller.user_id is None:
                return None
            return await service.build_user_context(
                user_id=caller.user_id, pod_id=pod_id
            )
        if caller.workload_id is None or caller.pod_id != pod_id:
            # A workload acts only in its own pod.
            return None
        principal_type = "AGENT" if caller.kind is CallerKind.AGENT else "FUNCTION"
        if caller.user_id is None:
            return await service.build_workload_context(
                principal_type=principal_type,
                principal_id=caller.workload_id,
                pod_id=pod_id,
            )
        ctx = await service.build_delegated_workload_context(
            user_id=caller.user_id,
            principal_type=principal_type,
            principal_id=caller.workload_id,
            pod_id=pod_id,
            is_default_pod_agent=caller.kind is CallerKind.AGENT
            and is_pod_default_agent(caller.workload_id, pod_id=pod_id),
            # The conversation, which is what approvals and the outside-text
            # mark are kept under. Without it the gate asked an empty session
            # and nothing a run read from outside ever held it here.
            delegation_session_id=str(caller.conversation_id)
            if caller.conversation_id is not None
            else None,
        )
        approve_for_this_context(ctx, caller.approved)
        return ctx


async def _held(ctx: Context, resource: ResourceRef | None) -> bool:
    return await outside_text_hold(ctx, Permissions.MACHINE_USE, resource) is not None


__all__ = ["DatabaseMachineFacts", "machine_resource"]
