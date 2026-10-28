"""A pod audio file, said aloud on a surface: the ``say`` tool's delivery.

Split out of :class:`SurfaceEgress` by use case. It is the one verb that loads
bytes before it can build its envelope, and the envelope it builds is the voice
ladder -- native voice note, then the same bytes as an attachment, then a link.
"""

from __future__ import annotations

from uuid import UUID

from app.core.file_types import is_untyped_mime
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts.tools import (
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.domain.envelope import EnvelopeVoice, SurfaceEnvelope
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent_surfaces.services.display_resource_content import (
    load_pod_file_bytes,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_display_resource_render_plan,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery

logger = get_logger(__name__)


async def send_voice_note(
    *,
    uow: SqlAlchemyUnitOfWork,
    delivery: SurfaceDelivery,
    conversation_id: UUID,
    path: str,
    caption: str | None,
) -> bool:
    """Deliver a pod audio file as a native voice note on the surface.

    Tries the platform's native voice note (Telegram sendVoice / audio
    message); falls back to a normal file attachment (an inline audio player on
    most platforms) and then a link.
    """
    target = await delivery.resolve_egress_target(conversation_id)
    if target is None:
        return False
    # The caption is model-authored — strip any reasoning before delivery.
    caption = sanitize_user_visible_text(caption) if caption else caption
    loaded = await load_pod_file_bytes(
        uow=uow,
        target=target,
        conversation_id=conversation_id,
        path=path,
    )
    if loaded is None:
        # A warning: `say` reads this False as "the person did not hear it".
        logger.warning(
            "agent_surfaces.egress.voice_note_not_fetched.degraded",
            conversation_id=str(conversation_id),
            path=path,
        )
        return False
    entity, content = loaded

    # `or` was not enough: a file stored without an extension is typed
    # `application/octet-stream`, which is truthy, so the fallback never
    # fired and Telegram was handed a blob where sendVoice wants OGG.
    mime = "audio/ogg" if is_untyped_mime(entity.mime_type) else entity.mime_type

    # Voice note, then the same bytes as an attachment (an audio player on most
    # platforms), then the link card; the envelope walks them.
    return await delivery.deliver_envelope(
        target,
        envelope=SurfaceEnvelope(
            voice=EnvelopeVoice(
                file_name=entity.name,
                content=content,
                mime_type=mime,
                caption=caption,
                fallback=build_display_resource_render_plan(
                    pod_id=target.pod_id,
                    request=DisplayResourceRequest(
                        type=DisplayResourceType.FILE, path=path
                    ),
                    conversation_id=conversation_id,
                ),
            )
        ),
        metadata=await delivery.egress_metadata(target),
        conversation_id=conversation_id,
    )
