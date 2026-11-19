"""Ref -> row -> authorization -> capabilities. The only way to a machine.

Every caller -- a `/machines` route, an agent tool, a function's SDK call --
names a machine by ref and says what it wants to do with it. This finds the
row and decides: a pod machine through the core authorizer, like every other
pod resource; a person's own machine and a connected one by the pure rules in
`domain/machine_access.py`. It either hands the machine back or raises a
refusal a person can act on. Nothing else turns a ref into a sandbox id, so
nothing else can get the rule wrong.

A machine the caller may not see is reported exactly as one that does not
exist: its name is itself something kept from those it is kept from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import (
    DEFAULT_MACHINE_SIZE,
    Machine,
    MachineKind,
    MachineRef,
    MachineRefKind,
    MachineSize,
)
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
    PodAccess,
    connected_machine_access,
    default_machine_access,
    is_shared,
    pod_machine_access,
)
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineHeldForApproval,
    MachineMoving,
    MachineNotFound,
)
from app.modules.workspace.infrastructure.machine_facts import DatabaseMachineFacts
from app.modules.workspace.infrastructure.machine_repository import MachineRepository

_VERBS = {
    MachineAccess.STATUS: "see",
    MachineAccess.USE: "use",
    MachineAccess.MANAGE: "change",
}


class FactsSource(Protocol):
    """What a decision reads beyond the caller and the row."""

    async def answers_member(self, caller: MachineCaller) -> bool: ...

    async def pod_access(
        self, caller: MachineCaller, machine: Machine
    ) -> PodAccess: ...

    async def pods_named(self, caller: MachineCaller, name: str) -> list[UUID]: ...

    async def held_for_outside_text(self, caller: MachineCaller) -> bool: ...


@dataclass(frozen=True, slots=True)
class ResolvedMachine:
    machine: Machine
    access: MachineAccess

    @property
    def sandbox_id(self) -> UUID:
        return self.machine.id


class MachineResolver:
    """Built per unit of work: every read it makes is in the caller's transaction."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        facts: FactsSource | None = None,
        default_size: MachineSize | None = None,
    ) -> None:
        self._uow = uow
        self._repository = MachineRepository(uow)
        self._facts = facts or DatabaseMachineFacts(uow)
        self._default_size = default_size

    async def resolve(
        self,
        ref: str | MachineRef,
        caller: MachineCaller,
        *,
        need: MachineAccess = MachineAccess.USE,
    ) -> ResolvedMachine:
        parsed = ref if isinstance(ref, MachineRef) else _parse(ref)
        machine = await self._find(parsed, caller)
        if machine is None:
            raise MachineNotFound(str(parsed))
        access, held = await self._decision(caller, machine)
        if held and access < need:
            raise MachineHeldForApproval(str(parsed))
        _require(access, need, parsed)
        if machine.moving and need >= MachineAccess.USE:
            # Every route, tool and SDK call reaches a machine through here: a
            # write between the size move's pack and its destroy, or onto the
            # new disk before the home is back, would be silently lost.
            raise MachineMoving(str(parsed))
        return ResolvedMachine(machine=machine, access=access)

    async def decide(self, caller: MachineCaller, machine: Machine) -> MachineAccess:
        access, _held = await self._decision(caller, machine)
        return access

    async def _decision(
        self, caller: MachineCaller, machine: Machine
    ) -> tuple[MachineAccess, bool]:
        """The caller's access, and whether use is only held for the approval
        of a run reading what people outside the pod wrote -- on a person's
        own machine as on a pod's: seen, not used, until its person says so."""
        if machine.kind is MachineKind.DEFAULT:
            access = default_machine_access(
                caller,
                machine,
                answers_member=caller.kind is CallerKind.AGENT
                and caller.user_id == machine.owner_id
                and await self._facts.answers_member(caller),
            )
            if access is MachineAccess.USE and caller.kind is CallerKind.AGENT:
                if await self._facts.held_for_outside_text(caller):
                    return MachineAccess.STATUS, True
            return access, False
        if machine.kind is MachineKind.POD:
            pod = await self._facts.pod_access(caller, machine)
            return pod_machine_access(caller, machine, pod), pod.held
        pod = (
            await self._facts.pod_access(caller, machine)
            if is_shared(machine) and caller.user_id != machine.owner_id
            else PodAccess()
        )
        return connected_machine_access(caller, machine, pod), pod.held

    async def visible(
        self, caller: MachineCaller, *, pod_ids: list[UUID]
    ) -> list[ResolvedMachine]:
        """Every machine this caller may at least see: their own, then each
        pod's. A person's default machine is created by being listed, so the
        Environments screen never has to explain an empty rail."""
        candidates: list[Machine] = []
        if caller.user_id is not None:
            await self._default_for(caller.user_id)
            candidates.extend(await self._repository.list_for_user(caller.user_id))
        for pod_id in pod_ids:
            candidates.extend(await self._repository.list_for_pod(pod_id))
        visible: list[ResolvedMachine] = []
        for machine in candidates:
            access = await self.decide(caller, machine)
            if access > MachineAccess.NONE:
                visible.append(ResolvedMachine(machine, access))
        return visible

    async def _find(self, ref: MachineRef, caller: MachineCaller) -> Machine | None:
        if ref.kind is MachineRefKind.ME:
            if caller.user_id is None:
                return None
            return await self._default_for(caller.user_id)
        if ref.kind is MachineRefKind.ID and ref.machine_id is not None:
            return await self._repository.get(ref.machine_id)
        if ref.kind is MachineRefKind.MINE and ref.name is not None:
            if caller.user_id is None:
                return None
            return await self._repository.connected(caller.user_id, ref.name)
        if ref.kind is MachineRefKind.POD and ref.name is not None:
            pod_id = await self._pod_of(ref, caller)
            if pod_id is None:
                return None
            return await self._repository.pod_machine(pod_id, ref.name)
        return None

    async def _pod_of(self, ref: MachineRef, caller: MachineCaller) -> UUID | None:
        """``pod:<name>`` is the caller's own pod; ``pod:<pod>:<name>`` takes a
        pod id, or the name of a pod the caller is in.

        A name two of the caller's pods share is not guessed between -- unless
        one is the pod the call is made in -- and is refused as not found, with
        the way out: the id.
        """
        if ref.pod is None:
            return caller.pod_id
        try:
            return UUID(ref.pod)
        except ValueError:
            pass
        named = await self._facts.pods_named(caller, ref.pod)
        if len(named) == 1:
            return named[0]
        if caller.pod_id is not None and caller.pod_id in named:
            return caller.pod_id
        if named:
            raise MachineNotFound(
                str(ref),
                hint=(
                    f"More than one of your pods is called '{ref.pod}'; name "
                    f"the pod by its id: pod:<pod-id>:{ref.name}."
                ),
            )
        return None

    async def _default_for(self, user_id: UUID) -> Machine:
        existing = await self._repository.default_for(user_id)
        if existing is not None:
            return existing
        return await self._repository.ensure_default(
            user_id, size=self._default_size or await self._policy_default(user_id)
        )

    async def _policy_default(self, user_id: UUID) -> MachineSize:
        from app.modules.workspace.services.machine_policy import policy_for_user

        policy = await policy_for_user(self._uow, user_id)
        return policy.default_size


def _parse(ref: str) -> MachineRef:
    try:
        return MachineRef.parse(ref)
    except ValueError:
        raise MachineNotFound(ref) from None


def _require(access: MachineAccess, need: MachineAccess, ref: MachineRef) -> None:
    if access >= need:
        return
    if access is MachineAccess.NONE:
        raise MachineNotFound(str(ref))
    raise MachineAccessDenied(str(ref), _VERBS[need])


__all__ = [
    "DEFAULT_MACHINE_SIZE",
    "MachineResolver",
    "ResolvedMachine",
]
