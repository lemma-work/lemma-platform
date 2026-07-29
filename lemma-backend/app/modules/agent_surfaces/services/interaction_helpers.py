from __future__ import annotations

from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.log.log import get_logger
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    parse_callback_id,
)

logger = get_logger(__name__)


def parse_interaction_target(parsed) -> tuple[UUID, str] | None:
    raw_target = parse_callback_id(parsed.callback_id)
    if raw_target is None or not raw_target[0]:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_unparseable_callback.diagnostic",
            callback_id=parsed.callback_id,
        )
        return None
    try:
        return UUID(raw_target[0]), raw_target[1]
    except ValueError:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_invalid_conversation.diagnostic",
            callback_id=parsed.callback_id,
        )
        return None


def interaction_sender_matches(link, parsed) -> bool:
    return not (
        link.external_user_id
        and parsed.external_user_id
        and link.external_user_id != parsed.external_user_id
    )


async def resolve_interaction_delivery(
    ingress,
    parsed,
    conversation_id: UUID,
):
    link = await ingress.conversation_link_repository.get_by_conversation_id(
        conversation_id
    )
    if link is None or link.platform != parsed.platform.value:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_no_matching.diagnostic",
            conversation_id=conversation_id,
        )
        return None
    return await _resolve_link_delivery(ingress, parsed, link)


async def resolve_current_interaction_delivery(ingress, parsed):
    external_thread_id = str(parsed.external_thread_id or "").strip()
    if not external_thread_id:
        return None
    surface_id = (
        await ingress.conversation_link_repository.find_surface_id_for_external_thread(
            platform=parsed.platform.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=external_thread_id,
            external_user_id=parsed.external_user_id,
        )
    )
    if surface_id is None:
        return None
    link = await ingress.conversation_link_repository.get_by_external_thread(
        surface_id=surface_id,
        platform=parsed.platform.value,
        external_channel_id=parsed.external_channel_id,
        external_thread_id=external_thread_id,
        external_user_id=parsed.external_user_id,
    )
    if link is None:
        return None
    return await _resolve_link_delivery(ingress, parsed, link)


async def _resolve_link_delivery(ingress, parsed, link):
    surface = await ingress.surface_repository.get(link.surface_id)
    if surface is None or not surface.is_active:
        logger.debug(
            "agent_surfaces.ingress_service."
            "surface_interaction_dropped_surface_missing.diagnostic",
            conversation_id=link.conversation_id,
            surface_id=link.surface_id,
        )
        return None
    adapter = ingress.adapter_registry.get(surface.surface_type)
    if adapter is None:
        return None
    credentials = await ingress._resolve_credentials(surface)
    return link, surface, adapter, credentials


async def retry_interaction_conversation(
    *,
    conversation_service,
    uow,
    conversation,
) -> None:
    auth_ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )
    token = set_current_context(auth_ctx)
    try:
        retry_service = ConversationRetryService(
            uow=conversation_service.uow,
            conversation_repository=conversation_service.conversation_repository,
            agent_repository=conversation_service.agent_repository,
            authorization_service=conversation_service.authorization_service,
            fallback_model_name=conversation_service.fallback_model_name,
            usage_service=conversation_service.usage_service,
        )
        await retry_service.retry_failed_run(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            agent_name=None,
        )
    finally:
        reset_current_context(token)
