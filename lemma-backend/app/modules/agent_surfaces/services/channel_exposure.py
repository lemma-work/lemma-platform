"""A member's files and rows, kept out of a group that people outside the pod read.

In a group, a member's run reads pod files and table rows with that member's
authority -- that is what being a member means. ``display_resource`` then posted
them to the whole chat, and the chat may hold a client in a WhatsApp group, the
other company in a Slack Connect channel, or strangers a group was opened to.
Nothing asked whether they should see what the member can.

The smallest safe behaviour, until there is an approval for it: the contents
are not posted. The chat gets a short note and the resource's link card, which
opens in Lemma and so only for people with access to the pod -- the member who
asked among them.

"Outside the pod" is what is known without reading history: Slack saying the
channel is shared with another organisation, and the pod's own record of the
group (shared outside, or opened to outsiders).
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.contracts.tools import (
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.models import SurfaceDisplayRenderPlan
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.surface_route_types import SurfaceEgressTarget

logger = get_logger(__name__)

#: The resources whose contents are read with the asking member's authority.
_MEMBER_DATA = frozenset({DisplayResourceType.FILE, DisplayResourceType.TABLE})

_WITHHELD_NOTE = (
    "People outside the pod are in this chat, so I haven't posted this here. "
    "It opens in Lemma for those who have access:"
)


def outsiders_would_see(
    target: SurfaceEgressTarget,
    request: DisplayResourceRequest,
) -> bool:
    """Whether showing this resource here would show a member's data to outsiders."""
    if (
        request.type not in _MEMBER_DATA
        or not target.is_group
        or target.surface.surface_type.is_email
    ):
        return False
    if target.event.metadata.get("is_ext_shared_channel"):
        return True
    group = target.source
    return group.shared_externally or group.welcomes_outsiders


async def deliver_withheld(
    delivery: SurfaceDelivery,
    target: SurfaceEgressTarget,
    *,
    render_plan: SurfaceDisplayRenderPlan,
    metadata: dict[str, object],
    conversation_id: UUID,
) -> bool:
    """The note and the link card, in place of the resource's contents."""
    logger.info(
        "agent_surfaces.channel_exposure.resource_withheld",
        conversation_id=str(conversation_id),
        platform=target.surface.surface_type.value,
    )
    return await delivery.deliver_envelope(
        target,
        envelope=SurfaceEnvelope(text=_WITHHELD_NOTE, resources=[render_plan]),
        metadata=metadata,
        conversation_id=conversation_id,
    )
