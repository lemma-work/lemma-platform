"""Self-contained surface delivery for the ``display_resource`` tool.

The ``display_resource`` tool delivers a resource to a third-party chat surface
itself (rather than the run observer re-parsing the event stream). It calls
``deliver_display_resource_to_surface`` with the validated request; this module
owns the unit-of-work + ingress-service construction so the tool needs no
surface-specific wiring on its context.

Works uniformly for both agent harnesses: the in-process LEMMA harness and the
daemon harness (whose MCP tool calls execute in the backend) both reach this the
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
from app.modules.agent.tools.user_interaction.models import DisplayResourceRequest
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)

logger = get_logger(__name__)


def build_agent_surface_ingress_service(
    uow: SqlAlchemyUnitOfWork,
) -> AgentSurfaceIngressService:
    """Construct the ingress service from a unit of work.

    Mirrors ``AppWorkerContext.build_surface_event_handler`` and the FastAPI
    ``build_surface_event_handler`` dependency; kept here so the tool delivery
    path does not depend on the worker/request context.
    """
    from app.modules.agent.api.dependencies import get_conversation_service
    from app.modules.agent_surfaces.api.dependencies import surface_repository_factory
    from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (
        SqlAlchemySurfaceRoutingResolutionAdapter,
    )
    from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
        SurfaceConversationLinkRepository,
    )
    from app.modules.connectors.api.dependencies import get_connector_service

    return AgentSurfaceIngressService(
        uow=uow,
        surface_repository=surface_repository_factory(uow),
        conversation_link_repository=SurfaceConversationLinkRepository(uow),
        conversation_service=get_conversation_service(uow),
        connector_service=get_connector_service(uow),
        pod_membership_port=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
    )


async def deliver_display_resource_to_surface(
    *,
    conversation_id: UUID,
    request: DisplayResourceRequest,
    tool_call_id: str | None,
    tool_output: object | None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Deliver one display resource to the conversation's chat surface.

    Returns True when delivered, False when the conversation has no active
    surface egress target. Never raises — delivery is best-effort and must not
    abort the agent run.
    """
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = build_agent_surface_ingress_service(uow)
            return await service.send_display_resource_for_conversation(
                conversation_id=conversation_id,
                request=request,
                tool_call_id=tool_call_id,
                tool_output=tool_output,
                metadata=metadata,
            )
    except Exception as exc:
        logger.warning(
            "Surface display resource delivery failed conversation=%s tool_call=%s error=%s",
            conversation_id,
            tool_call_id,
            exc,
        )
        return False


async def deliver_surface_message_to_surface(
    *,
    conversation_id: UUID,
    message: str,
) -> bool:
    """Deliver a plain message to the conversation's chat surface now.

    Backs the current-user ``surface_send_message`` agent tool. Best-effort:
    returns False (never raises) when there is no active surface egress target.
    """
    try:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            service = build_agent_surface_ingress_service(uow)
            return await service.send_agent_message_for_conversation(
                conversation_id=conversation_id,
                message=message,
            )
    except Exception as exc:
        logger.warning(
            "Surface message delivery failed conversation=%s error=%s",
            conversation_id,
            exc,
        )
        return False


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
            service = build_agent_surface_ingress_service(uow)
            return await service.send_voice_note_for_conversation(
                conversation_id=conversation_id,
                path=file_path,
                caption=caption,
            )
    except Exception as exc:
        logger.warning(
            "Surface voice note delivery failed conversation=%s path=%s error=%s",
            conversation_id,
            file_path,
            exc,
        )
        return False
