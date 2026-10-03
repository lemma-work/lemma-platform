"""Self-contained surface delivery for the ``display_resource`` tool.

The ``display_resource`` tool delivers a resource to a third-party chat surface
itself (rather than the run observer re-parsing the event stream). It calls
``deliver_display_resource_to_surface`` with the validated request; this module
owns the unit-of-work + egress construction so the tool needs no
surface-specific wiring on its context.

Works uniformly for both agent harnesses: the in-process LEMMA harness and the
remote harness (whose MCP tool calls execute in the backend) both reach this the
same way — each call opens its own short uow. Credentials are resolved by the
ingress service per call; ``display_resource`` is infrequent so this is not a hot
path.

Email surfaces (Gmail/Outlook) are intentionally NOT delivered here — their
resources are accumulated by the run observer into a single composed reply.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.log.log import get_logger
from app.modules.agent.contracts import DisplayResourceRequest
from app.modules.agent_surfaces.domain.delivery_result import SurfaceDeliveryResult
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress

logger = get_logger(__name__)


def build_egress(uow: SqlAlchemyUnitOfWork) -> SurfaceEgress:
    """The outbound objects, from a unit of work.

    One function, in `composition`. This was the third copy of the same
    four-argument ingress constructor; its predecessor here said it "mirrors"
    the other two and named a fourth that no longer exists. What the three
    functions below actually need is the sending half, which is now its own
    object.
    """
    from app.modules.agent_surfaces.composition import build_surface_egress

    return build_surface_egress(uow)


async def deliver_display_resource_to_surface(
    *,
    conversation_id: UUID,
    request: DisplayResourceRequest,
    tool_call_id: str | None,
    tool_output: object | None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Deliver one display resource to the conversation's chat surface.

    Returns True when delivered, False when it was not -- either the
    conversation has no active surface egress target or the platform refused it.
    Never raises: delivery must not abort the agent run. The caller has to read
    the answer, though, because the model will otherwise believe a file it was
    never able to show has been shown; a failure is logged as a warning here so
    it is visible even where the caller ignores it.
    """
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = build_egress(uow)
            return bool(
                await service.send_display_resource_for_conversation(
                    conversation_id=conversation_id,
                    request=request,
                    tool_call_id=tool_call_id,
                    tool_output=tool_output,
                    metadata=metadata,
                )
            )
    except Exception:
        logger.warning(
            "agent_surfaces.surface_display_delivery.display_resource_delivery_failed.degraded",
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            exc_info=True,
        )
        return False


async def deliver_surface_message_to_surface(
    *,
    conversation_id: UUID,
    message: str,
) -> SurfaceDeliveryResult:
    """Deliver a plain message to the conversation's chat surface now.

    Backs the current-user ``surface_send_message`` agent tool. Best-effort:
    undelivered (never raises) when there is no active surface egress target,
    and says so when a closed reply window sent it by email instead.
    """
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = build_egress(uow)
            return await service.send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=message,
            )
    except Exception:
        logger.warning(
            "agent_surfaces.surface_display_delivery.surface_message_delivery_failed.degraded",
            conversation_id=conversation_id,
            exc_info=True,
        )
        return SurfaceDeliveryResult.undelivered()


async def deliver_voice_note_to_surface(
    *,
    conversation_id: UUID,
    file_path: str,
    caption: str | None = None,
) -> bool:
    """Deliver a pod audio file to the conversation's surface as a voice note.

    Called by the ``say`` tool. Returns True when delivered, False when there is
    no active surface egress target. Never raises — best-effort, must not abort
    the agent run.
    """
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = build_egress(uow)
            return bool(
                await service.send_voice_note_for_conversation(
                    conversation_id=conversation_id,
                    path=file_path,
                    caption=caption,
                )
            )
    except Exception:
        logger.warning(
            "agent_surfaces.surface_display_delivery.voice_note_delivery_failed.degraded",
            conversation_id=conversation_id,
            exc_info=True,
        )
        return False
