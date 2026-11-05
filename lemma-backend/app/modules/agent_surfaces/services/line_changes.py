"""A platform's edit or deletion of a message, applied to the line it was heard as.

The timeline is the record of what was said, so a message edited on Slack
reads as it says now -- to the members reading the channel and to every run
whose background holds that line -- and a deleted one is deleted here too:
emptied in place, keeping its number (``channels.contracts.erase_line``). An
edit is read the way the message was heard (``LineMarkup``), so whom it names
is synced with what it says (``channels.contracts.revise_line``), and both go
through the channel's own write path, which is what tells its live readers.

Only a line heard at that place: the chat the platform names among the
receiving surfaces' places -- kept to the workspace or tenant the event names
where a bot is installed per workspace -- or, said privately, the sender's own
place. A line the bot carried there, its own answers among them, is the pod's
and not the platform's to change.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import platform_value_for_source
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.line_changes import (
    LineChangeKind,
    ParsedLineChange,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.line_markup import Bot, LineMarkup
from app.modules.channels.contracts import (
    Source,
    erase_line,
    revise_line,
)
from app.modules.channels.contracts.backgrounds import heard_at
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.places import person_places, places_hearing

logger = get_logger(__name__)

#: Platforms whose bots are installed per workspace or tenant, and whose
#: events name it.
_PER_WORKSPACE = frozenset({"SLACK", "TEAMS"})


async def apply_line_changes(
    uow: SqlAlchemyUnitOfWork,
    request: SurfaceIngressRequest,
    *,
    adapters: SurfacePlatformAdapterRegistry,
) -> int:
    """Apply every edit or deletion this webhook body carries; how many lines
    changed. Most bodies carry none."""
    surface_ids: list[UUID] | None
    if isinstance(request, SurfacePlatformWebhookIngress):
        platform = platform_value_for_source(request.source)
        surface_ids = request.receiver_surface_ids
    else:
        surface = await SurfaceRepository(uow).get(request.surface_id)
        if surface is None:
            return 0
        platform = surface.surface_type.value
        surface_ids = [surface.id]
    adapter = adapters.get(platform) if platform else None
    if adapter is None:
        return 0
    changed = 0
    for change in adapter.parse_line_changes(request.payload):
        for source in await _places(uow, change, surface_ids=surface_ids):
            changed += await _apply(uow, change, source)
    return changed


async def _places(
    uow: SqlAlchemyUnitOfWork,
    change: ParsedLineChange,
    *,
    surface_ids: Sequence[UUID] | None,
) -> list[Source]:
    """The places the changed message can have been heard at."""
    if change.is_dm:
        # A person's place is asked for only among the receiving surfaces:
        # nothing narrower than the platform names whose it would be.
        found = [
            source
            for sender in (change.sender_external_ids if surface_ids else ())
            for source in await person_places(
                uow,
                platform=change.platform,
                external_user_id=sender,
                surface_ids=surface_ids,
            )
        ]
    elif change.external_channel_id:
        found = await places_hearing(
            uow,
            external_channel_id=change.external_channel_id,
            surface_ids=surface_ids,
            platform=change.platform if surface_ids is None else None,
        )
    else:
        found = []
    if change.platform not in _PER_WORKSPACE:
        return found
    surfaces = SurfaceRepository(uow)
    kept: list[Source] = []
    for source in found:
        surface = await surfaces.get(source.surface_id) if source.surface_id else None
        if surface is not None and surface.matches_tenant(change.tenant_id):
            kept.append(source)
    return kept


async def _apply(
    uow: SqlAlchemyUnitOfWork, change: ParsedLineChange, source: Source
) -> int:
    """Change the line heard at this place as this message, if there is one."""
    heard = await heard_at(
        uow, source_id=source.id, external_message_ids=[change.external_message_id]
    )
    ref = heard.get(change.external_message_id)
    line = await line_by_id(uow, ref.line_id) if ref and ref.line_id else None
    if line is None or line.source_id != source.id or line.is_deleted:
        return 0
    if change.kind is LineChangeKind.DELETED:
        done = await erase_line(uow, message_id=line.id)
    else:
        if change.edited is None:
            return 0
        said = await LineMarkup(uow).read(
            change.edited,
            pod_id=line.pod_id,
            channel_id=line.channel_id,
            bot=Bot(surface_id=source.surface_id),
        )
        if not said.text.strip():
            return 0
        if said.text == line.text and tuple(said.parts) == tuple(line.parts):
            # A link unfurled, a file finished processing: nothing it says.
            return 0
        done = await revise_line(
            uow,
            message_id=line.id,
            text=said.text,
            parts=said.parts,
            mentions=said.mentions,
            mentioned_agents=said.agents,
        )
    logger.info(
        "agent_surfaces.line_changes.applied.observed",
        platform=change.platform,
        kind=change.kind.value,
        changed=done is not None,
    )
    return int(done is not None)


__all__ = ["apply_line_changes"]
