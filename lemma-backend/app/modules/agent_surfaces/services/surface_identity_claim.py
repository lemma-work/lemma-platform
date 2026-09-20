"""Creating a surface that has to take something scarce on the way in.

Two platforms need an identity allocated before the row exists: Resend mints an
address off a catch-all domain, and WhatsApp draws a number from the pool. Both
are allocated per surface off a shared credential, and for both the arbiter is a
unique index on the row being written -- so allocation cannot live inside
`create_surface`, and a pre-check would still race.

A function taking the service rather than a method on it, for the same reason
`surface_bulk_teardown` is: it dispatches to two provisioning modules that each
call back into the service, so a method here would be a third place that knows
about both while owning neither. The service stayed at the 600-line ceiling with
it inside, which is the ratchet noticing the same thing.

Only the two callers a person drives come through here -- the surfaces API and
the bundle applier. `_ensure_shared_surface` deliberately does not, so every
personal pod keeps riding the shared line instead of each consuming a number.
"""

from __future__ import annotations

from uuid import UUID

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.authorization.context import Context
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)


async def _wants_a_pooled_number(
    uow,
    *,
    platform: SurfacePlatform,
    credential_mode: SurfaceCredentialMode | None,
    account_id: UUID | None,
) -> bool:
    """Is this a WhatsApp surface that should be given a number of its own?

    Four conditions, and each excludes a surface that would be wrong to allocate
    to: another platform, one bringing its own connected account, one explicitly
    not on system credentials, and -- last, because it costs a read -- a
    deployment with no pool, which keeps the shared line and the behaviour it
    had before any of this existed.
    """
    if platform is not SurfacePlatform.WHATSAPP or account_id is not None:
        return False
    mode = credential_mode or SurfaceCredentialMode.SYSTEM
    if mode is not SurfaceCredentialMode.SYSTEM:
        return False
    return await WhatsAppNumberRepository(uow).any_allocatable()


async def create_surface_claiming_identity(
    service,
    *,
    pod_id: UUID,
    agent_id: UUID | None,
    agent_name: str | None,
    platform: SurfacePlatform,
    name: str | None = None,
    config: SurfaceConfig | None = None,
    credential_mode: SurfaceCredentialMode | None = None,
    account_id: UUID | None = None,
    ctx: Context | None = None,
) -> AgentSurfaceEntity:
    """:meth:`create_surface`, claiming a scarce identity when one is needed.

    For the two callers a person drives — the surfaces API and the bundle
    applier. They bring their own name, config and credentials, so they
    cannot use ``provision_email_surface``, and calling ``create_surface``
    straight through is what used to land them on the ``pod-<hex>@``
    fallback: unreadable, and never screened for reserved local parts.

    Takes the agent's id and name rather than the agent, because those are
    the two things minting needs and both callers already hold them.
    """
    from app.modules.agent_surfaces.services.email_surface_provisioning import (
        create_surface_on_minted_address,
    )
    from app.modules.agent_surfaces.services.whatsapp_surface_provisioning import (
        provision_pooled_whatsapp_surface,
    )

    uow = service.surface_repository.uow
    # A WhatsApp surface somebody asked for gets a number of its own, the
    # way a Resend surface gets an address. Only when a pool exists: a
    # deployment that never added a number keeps the shared line and the
    # behaviour it had before there was a pool, which is what lets this ship
    # without touching anybody. A pool that exists but is empty for this
    # organisation is a 503 from below, not a silent fall back to the shared
    # line -- they asked for their own number and would find out it was not
    # theirs when a reply arrived from it.
    #
    # `_ensure_shared_surface` does not come through here, deliberately, so
    # every personal pod keeps riding the shared line rather than each
    # consuming a number out of the pool.
    if await _wants_a_pooled_number(
        uow, platform=platform, credential_mode=credential_mode, account_id=account_id
    ):
        return await provision_pooled_whatsapp_surface(
            uow,
            service=service,
            pod_id=pod_id,
            agent_id=agent_id or pod_id,
            organization_id=await service.surface_repository.organization_for_pod(
                pod_id
            ),
            name=name,
            ctx=ctx,
        )

    return await create_surface_on_minted_address(
        service,
        service.surface_repository.uow,
        pod_id=pod_id,
        # No agent named means the pod's own assistant, whose row id is
        # the pod's. The *name* stays None regardless, because it is what
        # the address is built from and the assistant's stored name is the
        # internal `pod_default` -- that would mint `pod-default.acme@` for
        # a pod that answers at `acme@`.
        agent_id=agent_id or pod_id,
        agent_name=agent_name,
        platform=platform,
        name=name,
        config=config or SurfaceConfig(),
        credential_mode=credential_mode,
        account_id=account_id,
        ctx=ctx,
    )
