"""Which surface a received email was delivered for.

The Resend webhook and the polling receiver both start from the same question --
"which pod's mailbox is this?" -- and must answer it identically, or the two
routes of one email land on different surfaces.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort


async def surface_for_recipients(
    repository: SurfaceInstallationRepositoryPort,
    normalized: dict[str, Any],
    recipients: list[str],
) -> AgentSurfaceEntity | None:
    """The surface this mail was delivered for, stamping the address that matched.

    Every address it was delivered for is tried, not just the one the sender
    typed: under aliasing or forwarding the pod's address is in ``received_for``
    and matching on ``to`` alone loses the mail.
    """
    for address in recipients:
        surface = await repository.get_active_by_address(
            platform=SurfacePlatform.RESEND.value, address=address
        )
        if surface is not None:
            normalized["to"] = address
            return surface
    return None
