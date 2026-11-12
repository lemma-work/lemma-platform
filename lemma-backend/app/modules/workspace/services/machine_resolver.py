"""Ref -> row -> authorization -> capabilities. The only way to a machine.

Every caller -- a `/machines` route, an agent tool, a function's SDK call --
names a machine by ref and says what it wants to do with it. This finds the
row, decides with the matrix in `domain/machine_access.py`, and either hands
the machine back or raises a refusal a person can act on. Nothing else turns
a ref into a sandbox id, so nothing else can get the rule wrong.

A machine the caller may not see is reported exactly as one that does not
exist: the name of a RESTRICTED machine is itself something kept from them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workspace.domain.machine import (
    DEFAULT_MACHINE_SIZE,
    Machine,
    MachineRef,
    MachineRefKind,
    MachineSize,
)
from app.modules.workspace.domain.machine_access import (
    AccessDecision,
    DenialReason,
    MachineAccess,
    MachineCaller,
    MachineFacts,
    decide,
)
from app.modules.workspace.domain.machine_errors import (
    MachineAccessDenied,
    MachineConsentRequired,
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
    async def gather(self, caller: MachineCaller, machine: Machine) -> MachineFacts: ...


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
        decision = await self.decide(caller, machine)
        _require(decision, need, parsed, caller)
        return ResolvedMachine(machine=machine, access=decision.access)

    async def decide(self, caller: MachineCaller, machine: Machine) -> AccessDecision:
        return decide(caller, machine, await self._facts.gather(caller, machine))

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
            decision = await self.decide(caller, machine)
            if decision.access > MachineAccess.NONE:
                visible.append(ResolvedMachine(machine, decision.access))
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
        """``pod:<name>`` is the caller's own pod; ``pod:<pod>/<name>`` takes a
        pod id, or the name of the caller's pod."""
        if ref.pod is None:
            return caller.pod_id
        try:
            return UUID(ref.pod)
        except ValueError:
            pass
        if caller.pod_id is None:
            return None
        from app.modules.pod.contracts.members import pod_name

        name = await pod_name(self._uow.session, caller.pod_id)
        if name is not None and name.strip().lower() == ref.pod.strip().lower():
            return caller.pod_id
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


def _require(
    decision: AccessDecision,
    need: MachineAccess,
    ref: MachineRef,
    caller: MachineCaller,
) -> None:
    if decision.access >= need:
        return
    if decision.reason is DenialReason.CONSENT_REQUIRED:
        raise MachineConsentRequired(str(caller.app_id), caller.app_name)
    if decision.access is MachineAccess.NONE:
        raise MachineNotFound(str(ref))
    raise MachineAccessDenied(str(ref), _VERBS[need])


__all__ = [
    "DEFAULT_MACHINE_SIZE",
    "MachineResolver",
    "ResolvedMachine",
]
