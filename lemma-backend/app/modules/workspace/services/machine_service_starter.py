"""Whether the one who started a service may still start it.

A service comes back after every pause with a token minted afresh for the
claims its first start used. Minting that token is a new grant of authority,
so it is decided again, the way the first start was: through the machine
resolver, as the caller that started it. A person who left the pod, an agent
whose ``machine.use`` was taken away, a run that no longer answers its
person's own machine -- each is refused at the restart as it would be at a
new start, and the service is forgotten rather than started with a token its
starter could no longer get.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.services.machine_services import (
    ServiceDefinition,
    TokenClaims,
)


def starter_of(
    claims: TokenClaims | None, started_by: UUID | None
) -> MachineCaller | None:
    """The caller that started a service, rebuilt from what was kept of it.

    None for a service that carries no Lemma authority to re-grant: one
    started by a person-less function run, whose restart mints no token.
    One with no claims but a person behind it (an app's page, which gets no
    token) is checked as that person: it is their use of the machine.
    """
    if claims is None:
        if started_by is None:
            return None
        return MachineCaller(CallerKind.USER, user_id=started_by)
    if claims.workload_type is None:
        return MachineCaller(CallerKind.USER, user_id=claims.user_id)
    kind = (
        CallerKind.FUNCTION
        if claims.workload_type.lower() == "function"
        else CallerKind.AGENT
    )
    return MachineCaller(
        kind,
        user_id=claims.user_id,
        pod_id=claims.pod_id,
        # The pod's own assistant is named by its pod, as the agent tools do.
        workload_id=claims.workload_id or claims.pod_id,
        conversation_id=claims.conversation_id,
    )


async def starter_may_restart(
    uow_factory: UnitOfWorkFactory, definition: ServiceDefinition
) -> bool:
    """Whether the service's starter may still use its machine."""
    from app.modules.workspace.infrastructure.machine_repository import (
        MachineRepository,
    )
    from app.modules.workspace.services.machine_resolver import MachineResolver

    caller = starter_of(definition.claims, definition.started_by)
    if caller is None:
        return True
    async with uow_factory() as uow:
        machine = await MachineRepository(uow).get(definition.machine_id)
        if machine is None:
            return False
        access = await MachineResolver(uow).decide(caller, machine)
    return access >= MachineAccess.USE


__all__ = ["starter_may_restart", "starter_of"]
