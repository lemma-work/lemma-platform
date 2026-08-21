from __future__ import annotations

from contextlib import suppress
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter

from sqlalchemy.exc import SQLAlchemyError

from app.modules.agent_surfaces.services.surface_configuration import (
    SurfaceConfigurationMixin,
)
from app.modules.agent_surfaces.services.surface_progress import (
    SurfaceProgressMixin,
)
from app.modules.agent_surfaces.services.surface_egress import SurfaceEgressMixin
from app.modules.agent_surfaces.services.surface_interactions import (
    SurfaceInteractionMixin,
)
from app.modules.agent_surfaces.services.surface_routing import SurfaceRoutingMixin
from app.modules.agent_surfaces.services.surface_conversation_links import (
    SurfaceConversationLinkMixin,
)
from app.modules.agent_surfaces.services.surface_ingress_credentials import (
    SurfaceIngressCredentialMixin,
)
from app.modules.agent_surfaces.services.surface_inbound import SurfaceInboundMixin
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.composition.surface_agent import ConversationService
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
    SurfacePodMembershipPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.telegram_command_service import (
    handle_telegram_command,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
    SurfaceFileIngestService,
)
from app.composition.surface_connectors import ConnectorService
from app.core.log.log import get_logger

logger = get_logger(__name__)

_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15


class AgentSurfaceIngressService(
    SurfaceConfigurationMixin,
    SurfaceProgressMixin,
    SurfaceRoutingMixin,
    SurfaceConversationLinkMixin,
    SurfaceIngressCredentialMixin,
    SurfaceInboundMixin,
    SurfaceEgressMixin,
    SurfaceInteractionMixin,
):
    def __init__(
        self,
        *,
        uow=None,
        uow_factory: UnitOfWorkFactory | None = None,
        surface_repository: SurfaceInstallationRepositoryPort | None = None,
        conversation_link_repository: SurfaceConversationLinkRepository | None = None,
        conversation_service: ConversationService | None = None,
        connector_service: ConnectorService | None = None,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
        pod_membership_port: SurfacePodMembershipPort | None = None,
        file_ingest_service: SurfaceFileIngestService | None = None,
        conversation_service_factory: Callable[[Any], ConversationService]
        | None = None,
        connector_service_factory: Callable[[Any], ConnectorService] | None = None,
    ):
        # Two modes:
        #  - uow mode (request/egress/ingress callers): collaborators are bound
        #    to one request-scoped session — fine for short HTTP/event handlers.
        #  - uow_factory mode (the worker's execute_chat): the long external I/O
        #    (platform APIs, file ingest, transcription) must NOT pin a pooled
        #    connection, so the credential read and the message-write tail each
        #    open their own short UoW via the factories.
        if uow is None and uow_factory is None:
            raise ValueError(
                "AgentSurfaceIngressService requires either uow or uow_factory"
            )
        self.uow = uow
        self._uow_factory = uow_factory
        self._conversation_service_factory = conversation_service_factory
        self._connector_service_factory = connector_service_factory
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.conversation_service = conversation_service
        self.connector_service = connector_service
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.file_ingest_service = file_ingest_service or SurfaceFileIngestService(
            adapter_registry=self.adapter_registry
        )
        self.event_dedup_store = event_dedup_store or get_surface_event_dedup_store()
        self.pod_membership_port = pod_membership_port
        # uow-bound collaborators are only used by the request/egress/ingress
        # paths; the worker execute_chat path opens its own short UoWs instead.
        if uow is not None:
            self.external_user_repository = ExternalSurfaceUserRepository(uow)
            self.identity_service = SurfaceIdentityResolutionService(
                uow, self.external_user_repository
            )
            self.credential_resolver = SurfaceCredentialResolver(
                session=uow.session,
                connector_service=connector_service,
            )
        else:
            self.external_user_repository = None
            self.identity_service = None
            self.credential_resolver = None

    async def prepare_ingress(
        self, request: SurfaceIngressRequest
    ) -> AgentSurfaceContext | None:
        if isinstance(request, SurfacePlatformWebhookIngress):
            return await self._prepare_platform_webhook_ingress(request)
        if isinstance(request, SurfaceDirectWebhookIngress):
            return await self._prepare_surface_webhook_ingress(request)
        return await self._prepare_schedule_ingress(request)

    async def execute_chat(self, context: dict[str, Any] | AgentSurfaceContext) -> None:
        parsed_context = (
            context
            if isinstance(context, (SurfaceChatContext, SurfaceReplyContext))
            else TypeAdapter(AgentSurfaceContext).validate_python(context)
        )
        platform = parsed_context.platform
        adapter = self.adapter_registry.get(platform)
        if adapter is None:
            return

        (
            parsed_context.message_metadata.event_metadata
            if isinstance(parsed_context, SurfaceChatContext)
            else {}
        )

        if isinstance(parsed_context, SurfaceReplyContext):
            # Credentials are needed only to send the automated fallback.
            credentials = await self._resolve_credentials_from_context(parsed_context)
            await deliver_fallback_reply(
                adapter=adapter,
                context=parsed_context,
                credentials=credentials,
            )
            return

        await self.start_agent_chat(parsed_context)

    async def start_agent_chat(self, context: SurfaceChatContext) -> None:
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        credentials = await self._resolve_credentials_from_context(context)
        if await handle_telegram_command(
            context=context,
            adapter=adapter,
            credentials=credentials,
            uow_factory=self._uow_factory,
            conversation_service_factory=self._conversation_service_factory,
            uow=self.uow,
            conversation_service=self.conversation_service,
        ):
            return
        with suppress(Exception):
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )

        # Auto-ingest any user-provided files into the pod datastore (/me/{platform})
        # so surface files behave like web uploads; failures never block the run.
        ingested: list[IngestedAttachment] = []
        if context.pod_id is not None:
            with suppress(Exception):
                ingested = await self.file_ingest_service.ingest_attachments(
                    pod_id=context.pod_id,
                    platform=context.platform,
                    user_id=context.user_id,
                    parsed=context.event,
                    credentials=credentials,
                )

        metadata = context.message_metadata.as_message_metadata()
        metadata.update(
            {
                "source": "agent_surfaces",
                "surface_id": str(context.surface_id) if context.surface_id else None,
                "external_user_id": context.message_external_user_id,
                "external_message_id": context.message_external_message_id,
            }
        )
        if ingested:
            metadata["ingested_files"] = [item.path for item in ingested]

        # Group/channel continuity: each user has a separate conversation, so fetch
        # the last few thread/channel messages fresh for THIS run and hand them to
        # the agent as background context. Best-effort; never blocks the run.
        if not context.event.is_dm:
            channel_context = await self._fetch_channel_context(
                adapter=adapter, context=context, credentials=credentials
            )
            if channel_context:
                metadata["channel_context"] = channel_context

        # Transcribe inbound voice notes here so the agent just reads the user's
        # words; the audio file stays saved for replay / re-listening.
        message_text = await self._transcribe_voice_attachments(
            ingested=ingested,
            original_text=context.message_text,
            metadata=metadata,
        )
        # The only DB writes happen here, AFTER all the external I/O above — so
        # in worker (factory) mode a pooled connection is held just for this
        # short tail, not across the platform/file/transcription calls.
        # A brand-new conversation is the one moment worth naming the thread on
        # the platform, so Slack's own DM history reads as a list of topics
        # rather than a stack of identical bot threads. Best-effort by
        # construction: the adapter returns False where it is unsupported.
        if context.created_conversation_title:
            try:
                await adapter.set_thread_title(
                    credentials=credentials,
                    event=context.event,
                    title=context.created_conversation_title,
                )
            except SQLAlchemyError:
                logger.debug(
                    "agent_surfaces.ingress_service.surface_thread_title_set.diagnostic",
                    conversation_id=context.conversation_id,
                )

        run_result = await self._commit_inbound_message(context, message_text, metadata)
        if run_result is not None and not run_result.started_new_run:
            await adapter.send_message(
                credentials=credentials,
                event=context.event,
                message=(
                    "Got it — I added that while I’m working. "
                    "I’ll carry it into the next turn."
                ),
            )
