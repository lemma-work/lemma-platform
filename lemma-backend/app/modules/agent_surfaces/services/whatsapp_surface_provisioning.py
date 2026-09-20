"""Give a surface a number out of the pool, or say plainly that there is none.

The counterpart of `email_surface_provisioning`, and deliberately shaped like
it: allocation lives beside surface creation rather than inside it, because the
arbiter is a unique index on the row being created and a pre-check would still
race. `SurfaceService.create_surface` takes the allocated identity the way it
takes an allocated address -- it does not go looking for one, and a surface that
arrives there without one is a surface on the shared line, which is a legitimate
thing to be.

Where it differs from email, and the difference is the interesting part.
An address is *generated*, so the supply is effectively infinite and running out
means a handful of random suffixes all collided -- a degenerate state worth
logging. A number is *bought*, so the supply is finite and "all of them are
held" is an ordinary Tuesday. That is why exhaustion here raises rather than
degrades, and why it is a 503 rather than a 409: this organisation conflicts
with nobody, the deployment simply has nothing left to give.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.uow import IUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfaceConfig,
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceNumberPoolExhaustedError,
)
from app.modules.agent_surfaces.domain.whatsapp_numbers import WhatsAppNumberEntity
from app.modules.agent_surfaces.infrastructure.repositories.whatsapp_number_repository import (
    WhatsAppNumberRepository,
)

logger = get_logger(__name__)


async def provision_pooled_whatsapp_surface(
    uow: IUnitOfWork,
    *,
    service,
    pod_id: UUID,
    agent_id: UUID,
    organization_id: UUID,
    name: str | None = None,
    config: SurfaceConfig | None = None,
    ctx=None,
) -> AgentSurfaceEntity:
    """Create a WhatsApp surface holding a number of this organisation's own.

    Raises `AgentSurfaceNumberPoolExhaustedError` when the pool has nothing
    free. The caller asked for a number specifically -- offering the shared line
    instead would be answering a different question, and the person would find
    out only when a reply arrived from a number they do not recognise.

    The claim is the surface write itself, passed down so the unique index
    decides. Each attempt runs in its own savepoint inside the repository, so a
    number taken between the candidate read and the write costs one retry rather
    than this whole unit of work.

    ``config`` is carried through for the same reason the email branch carries
    it: the caller is ``POST /surfaces`` or the bundle applier, and what they
    sent is a send policy, an identity allow-list and a channel list that the
    person chose. Dropping it here did not fail -- ``create_surface`` has a
    default for every field -- so the API answered 200 with a surface configured
    as nothing was asked for, which is the worst shape a bug of this kind can
    take.
    """
    created: list[AgentSurfaceEntity] = []

    async def claim(number: WhatsAppNumberEntity) -> None:
        created.append(
            await service.create_surface(
                pod_id=pod_id,
                agent_id=agent_id,
                platform=SurfacePlatform.WHATSAPP,
                name=name,
                config=config,
                credential_mode=SurfaceCredentialMode.SYSTEM,
                surface_identity_id=number.phone_number_id,
                ctx=ctx,
            )
        )

    allocated = await WhatsAppNumberRepository(uow).allocate_for_organization(
        organization_id=organization_id,
        claim=claim,
    )
    if allocated is None or not created:
        raise AgentSurfaceNumberPoolExhaustedError(
            "Every WhatsApp number this deployment owns is already allocated. "
            "Add more to the pool with scripts/whatsapp_numbers.py, or delete a "
            "surface that is holding one."
        )
    logger.info(
        "agent_surfaces.whatsapp_surface_provisioning.number_allocated",
        organization_id=str(organization_id),
        pod_id=str(pod_id),
        phone_number_id=allocated.phone_number_id,
    )
    return created[0]
