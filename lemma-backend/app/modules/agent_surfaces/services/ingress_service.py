from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import AgentRunApprovalDecision
from app.composition.surface_agent import ConversationService
from app.modules.agent.contracts import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.platforms.attachment_limits import fits_inline
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.composition.surface_datastore import build_file_service
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    ResolvedSurfaceUser,
    SurfaceChannelRoute,
    SurfaceCredentialMode,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceMessageMetadata,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
    SurfacePlatformAdapterPort,
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
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    # Re-exported: ``_ask_user_request_dict`` still has a caller here (the
    # native-interaction path) and a unit test that imports it from this module.
    _ask_user_request_dict,
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
    identity_confirmation_context,
    nonmember_context,
    prepare_unrouted_context,
    surface_setup_context,
    unresolved_sender_context,
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
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
    build_ask_user_render_plan,
    build_display_resource_render_plan,
    merge_other_answers,
    render_questions_as_text,
)
from app.modules.agent_surfaces.services.interaction_helpers import (
    interaction_sender_matches,
    parse_interaction_target,
    resolve_current_interaction_delivery,
    resolve_interaction_delivery,
    retry_interaction_conversation,
)
from app.composition.surface_connectors import ConnectorService
from app.core.log.log import get_logger

logger = get_logger(__name__)

_CONVERSATION_TITLE_MAX_LENGTH = 120
# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15


@dataclass(frozen=True)
class ResolvedSurfaceRoute:
    agent_id: UUID | None
    agent_name: str | None
    agent_display_name: str
    conversation_kind: str
    route_key: str


@dataclass(frozen=True)
class _SurfaceEgressTarget:
    """Resolved destination for an outbound surface message (see
    :meth:`AgentSurfaceIngressService._resolve_egress_target`)."""

    link: AgentSurfaceConversationLink
    surface: AgentSurfaceEntity
    adapter: SurfacePlatformAdapterPort
    event: ParsedInboundSurfaceEvent
    credentials: dict[str, Any]


class AgentSurfaceIngressService:
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

    async def _prepare_platform_webhook_ingress(
        self, request: SurfacePlatformWebhookIngress
    ) -> AgentSurfaceContext | None:
        platform = self._resolve_platform(request.source)
        if not platform:
            return None

        adapter = self.adapter_registry.get(platform)
        if adapter is None:
            return None

        parsed = await adapter.parse_inbound_event(request.payload, request.headers)
        if parsed is None:
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_ignored_webhook_because.observed",
                source=request.source,
            )
            return None

        surfaces = await self.surface_repository.list_active_by_type(platform)

        # Scope to the bot that actually delivered this event when a native
        # receiver told us which surfaces it serves (Telegram polling / Slack
        # socket). This prevents a custom bot's update from being attributed to a
        # different bot's surface. A shared system-bot platform webhook leaves
        # this unset → platform-wide fan-in (disambiguated per-sender below).
        if request.receiver_surface_ids:
            allowed_ids = set(request.receiver_surface_ids)
            surfaces = [surface for surface in surfaces if surface.id in allowed_ids]
            if not surfaces:
                return None
        elif platform in {
            SurfacePlatform.TELEGRAM.value,
            SurfacePlatform.WHATSAPP.value,
        }:
            # Platform-wide webhooks are shared system credentials. Custom/bound
            # bots must arrive with receiver_surface_ids (native receiver) or via
            # a direct surface webhook; otherwise continuity for the same external
            # user/thread can accidentally pull a system-bot message into a
            # custom-bot conversation.
            surfaces = [
                surface
                for surface in surfaces
                if surface.account_id is None
                and surface.credential_mode is SurfaceCredentialMode.SYSTEM
            ]

        # Mention verification for Telegram groups: the parser records any
        # @username / text_mention entities but does NOT set mentioned_agent for
        # generic mentions (a `mention` entity is just a plain @username and
        # doesn't indicate *which* user was mentioned). Here we verify whether
        # the mention actually targets this bot by resolving the bot's
        # @username / user id via getMe and comparing. Must run before
        # allows_inbound_event so the event isn't filtered out before we get a
        # chance to check.
        if (
            platform == SurfacePlatform.TELEGRAM.value
            and not parsed.is_dm
            and not parsed.mentioned_agent
            and surfaces
            and (
                (parsed.metadata or {}).get("mentioned_usernames")
                or (parsed.metadata or {}).get("text_mention_user_ids")
                or "@" in (parsed.message_text or "")
            )
        ):
            parsed = await self._telegram_text_mention_enrich(parsed, surfaces[0])

        candidates = [
            surface for surface in surfaces if surface.allows_inbound_event(parsed)
        ]
        if not candidates:
            return await self._prepare_unrouted_platform_context(
                platform=platform,
                surface=self._scoped_fallback_surface(request, surfaces),
                parsed=parsed,
                adapter=adapter,
            )

        # Resolve the sender once (using the first candidate's credentials) and
        # pick the surface this event belongs to (continuity → pod membership →
        # user default → deterministic tiebreak). An unknown sender only proceeds
        # when the target surface is unambiguous — it gets the signup/link flow.
        identity_surface = candidates[0]
        resolved_user = await self._resolve_sender_identity(
            adapter=adapter,
            parsed=parsed,
            credentials=await self._resolve_credentials(identity_surface),
        )
        matched_surface = await self._select_surface(
            candidates=candidates,
            resolved_user=resolved_user,
            parsed=parsed,
            platform=platform,
        )
        if matched_surface is None and len(candidates) == 1 and parsed.is_dm:
            # Single unambiguous DM surface: route to it so the onboarding flow
            # runs — an unknown sender gets the signup link, a signed-up
            # non-member gets the pod-access link (see _prepare_surface_context).
            matched_surface = identity_surface

        if matched_surface is None:
            return await self._prepare_unrouted_platform_context(
                platform=platform,
                surface=identity_surface,
                parsed=parsed,
                adapter=adapter,
                resolved_user=resolved_user,
            )

        return await self._prepare_surface_context(
            surface=matched_surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
        )

    async def _prepare_surface_webhook_ingress(
        self,
        request: SurfaceDirectWebhookIngress,
    ) -> AgentSurfaceContext | None:
        surface = await self.surface_repository.get(request.surface_id)
        if surface is None:
            return None

        if not surface.is_active or not surface.status.accepts_inbound_events():
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None

        parsed = await adapter.parse_inbound_event(request.payload, request.headers)
        if parsed is None:
            return None

        return await self._prepare_surface_context(
            surface=surface,
            parsed=parsed,
            adapter=adapter,
        )

    async def _prepare_schedule_ingress(
        self,
        request: SurfaceScheduleIngress,
    ) -> AgentSurfaceContext | None:
        surface = await self.surface_repository.get_by_email_schedule_id(
            request.schedule_id
        )
        if surface is None:
            return None
        if not surface.is_active or not surface.status.accepts_inbound_events():
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None

        parsed = await adapter.parse_inbound_event(request.payload, {})
        if parsed is None:
            return None

        return await self._prepare_surface_context(
            surface=surface,
            parsed=parsed,
            adapter=adapter,
        )

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
        try:
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.adding_surface_processing_indicator_s.diagnostic'
            )

        # Auto-ingest any user-provided files into the pod datastore (/me/{platform})
        # so surface files behave like web uploads; failures never block the run.
        ingested: list[IngestedAttachment] = []
        if context.pod_id is not None:
            try:
                ingested = await self.file_ingest_service.ingest_attachments(
                    pod_id=context.pod_id,
                    platform=context.platform,
                    user_id=context.user_id,
                    parsed=context.event,
                    credentials=credentials,
                )
            except Exception:
                logger.debug(
                    'agent_surfaces.ingress_service.surface_file_auto_ingest_s.diagnostic'
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
            except Exception:
                logger.debug(
                    'agent_surfaces.ingress_service.surface_thread_title_set.diagnostic',
                    conversation_id=context.conversation_id,
                )

        run_result = await self._commit_inbound_message(
            context, message_text, metadata
        )
        if run_result is not None and not run_result.started_new_run:
            await adapter.send_message(
                credentials=credentials,
                event=context.event,
                message=(
                    "Got it — I added that while I’m working. "
                    "I’ll carry it into the next turn."
                ),
            )

    async def _commit_inbound_message(
        self,
        context: SurfaceChatContext,
        message_text: str,
        metadata: dict[str, Any],
    ):
        """Persist the inbound message / resume the paused run in a short UoW."""
        if self._uow_factory is not None:
            if self._conversation_service_factory is None:
                raise RuntimeError("Conversation service factory is unavailable")
            async with self._uow_factory() as uow:
                conversation_service = self._conversation_service_factory(uow)
                return await self._write_inbound_message(
                    context, message_text, metadata, uow, conversation_service
                )
        else:
            if self.uow is None or self.conversation_service is None:
                raise RuntimeError("Conversation service is unavailable")
            return await self._write_inbound_message(
                context, message_text, metadata, self.uow, self.conversation_service
            )

    async def _write_inbound_message(
        self,
        context: SurfaceChatContext,
        message_text: str,
        metadata: dict[str, Any],
        uow,
        conversation_service: ConversationService,
    ):
        if context.pod_id is None:
            raise ValueError("Surface chat context requires a pod")
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=context.user_id,
            pod_id=context.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            # If the run is paused on an ask_user, treat this inbound text as the
            # answer and resume — rather than starting a new message/run. This is
            # how the formatted-text fallback (and any "type your own" reply) gets
            # back into the run as a structured answer.
            if not await maybe_resume_pending_interaction(
                context, message_text, conversation_service=conversation_service
            ):
                return await conversation_service.add_user_message_and_start_run(
                    conversation_id=context.conversation_id,
                    user_id=context.user_id,
                    content=message_text,
                    pod_id=context.pod_id,
                    agent_name=context.agent_name,
                    message_metadata=metadata,
                )
            return None
        finally:
            reset_current_context(token)

    async def _fetch_channel_context(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        context: SurfaceChatContext,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Best-effort recent thread/channel messages for a group mention, as a
        list of ``{author, text, ts}`` dicts. Fetched fresh per run; never raises."""
        try:
            messages = await adapter.fetch_thread_context(
                credentials=credentials,
                event=context.event,
                limit=_CHANNEL_CONTEXT_LIMIT,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_channel_context_fetch_platform.diagnostic',
                conversation_id=context.conversation_id,
            )
            return []
        return [m.model_dump(mode="json") for m in messages][:_CHANNEL_CONTEXT_LIMIT]

    async def _transcribe_voice_attachments(
        self,
        *,
        ingested: list[IngestedAttachment],
        original_text: str | None,
        metadata: dict[str, Any],
    ) -> str:
        """Transcribe inbound voice notes and fold them into the message text.

        The transcript becomes the user's words so the agent just reads text.
        Join rules: caption + voice → both; voice-only → transcript alone;
        several voices → labelled concatenation. A failed/oversize/empty voice
        falls back to ``[voice message]`` (so a voice-only message is never an
        empty prompt) while the saved audio file stays available. Provenance
        (path + transcript + language) is recorded in ``metadata``.
        """
        original = (original_text or "").strip()
        audio_present = [item for item in ingested if item.is_audio]
        if not audio_present:
            return original

        to_transcribe = [item for item in audio_present if item.audio_bytes is not None]
        provider = None
        if to_transcribe:
            try:
                from app.composition.surface_agent import get_speech_provider

                provider = get_speech_provider()
            except Exception:
                logger.warning(
                    "agent_surfaces.ingress_service.speech_provider_unavailable_ingress_s.degraded"
                )
                provider = None

        async def _one(item: IngestedAttachment) -> tuple[IngestedAttachment, Any]:
            try:
                result = await provider.transcribe(
                    item.audio_bytes, mime=item.mime or "audio/ogg"
                )
                return item, result
            except Exception:
                logger.debug(
                    'agent_surfaces.ingress_service.surface_voice_transcription_path_s.diagnostic'
                )
                return item, None

        results: list[tuple[IngestedAttachment, Any]] = []
        if provider is not None and to_transcribe:
            results = list(
                await asyncio.gather(*[_one(item) for item in to_transcribe])
            )

        transcripts: list[str] = []
        provenance: list[dict[str, Any]] = []
        for item, result in results:
            text = (getattr(result, "text", "") or "").strip()
            if text:
                transcripts.append(text)
                provenance.append(
                    {
                        "path": item.path,
                        "text": text,
                        "detected_language": getattr(result, "detected_language", None),
                        "duration_seconds": getattr(result, "duration_seconds", None),
                    }
                )
            else:
                provenance.append({"path": item.path, "text": "", "failed": True})
        if provenance:
            metadata["voice_transcripts"] = provenance
        if not transcripts:
            metadata["voice_transcription_failed"] = True

        if not transcripts:
            combined = "[voice message]"
        elif len(transcripts) == 1:
            combined = transcripts[0]
        else:
            combined = "\n\n".join(
                f"[Voice {index}]\n{text}"
                for index, text in enumerate(transcripts, start=1)
            )

        if original:
            return f"{original}\n\n{combined}"
        return combined

    async def _resolve_egress_target(
        self, conversation_id: UUID
    ) -> "_SurfaceEgressTarget | None":
        """Resolve the surface/adapter/event for an outbound message.

        Returns None (never raises) when the conversation has no active surface
        link or its stored ``last_event`` is missing/unparseable, so callers in
        the agent-run path treat egress as best-effort.
        """
        link = await self.conversation_link_repository.get_by_conversation_id(
            conversation_id
        )
        if link is None:
            logger.debug(
                'agent_surfaces.ingress_service.surface_egress_skipped_no_conversation.diagnostic',
                conversation_id=conversation_id,
            )
            return None

        surface = await self.surface_repository.get(link.surface_id)
        if surface is None or not surface.is_active:
            logger.debug(
                'agent_surfaces.ingress_service.surface_egress_skipped_surface_missing.diagnostic',
                conversation_id=conversation_id,
                surface_id=link.surface_id,
            )
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            logger.debug(
                'agent_surfaces.ingress_service.surface_egress_skipped_no_adapter.diagnostic',
                surface_type=surface.surface_type,
                conversation_id=conversation_id,
            )
            return None

        if not link.last_event:
            logger.debug(
                'agent_surfaces.ingress_service.surface_egress_skipped_missing_last.diagnostic',
                conversation_id=conversation_id,
            )
            return None
        try:
            parsed_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_egress_skipped_invalid_last.diagnostic',
                conversation_id=conversation_id,
            )
            return None

        credentials = await self._resolve_credentials(surface)
        return _SurfaceEgressTarget(
            link=link,
            surface=surface,
            adapter=adapter,
            event=parsed_event,
            credentials=credentials,
        )

    async def _egress_metadata_with_agent_name(
        self,
        target: "_SurfaceEgressTarget",
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resolved = dict(metadata or {})
        agent_id = target.link.routed_agent_id or target.surface.agent_id
        # A pod-assistant route has no agent *by design*, so the usual
        # "fall back to the surface default" would put the default agent's name
        # and face on the pod assistant's replies.
        if self._routes_to_pod_assistant(target):
            agent_id = None
        agent = (
            await self.conversation_service.agent_repository.get(agent_id)
            if agent_id
            else None
        )
        resolved.setdefault(
            "agent_display_name", getattr(agent, "name", None) or "Lemma"
        )
        icon_url = getattr(agent, "icon_url", None)
        if icon_url:
            resolved.setdefault("agent_icon_url", str(icon_url))
        return resolved

    def _routes_to_pod_assistant(self, target: "_SurfaceEgressTarget") -> bool:
        """True when this conversation is answered by the pod assistant.

        Two ways to get there, and both have to be checked: a *channel* routed
        to it, or a *person* who chose it for their own DMs. Checking only the
        channel left every pod-assistant DM wearing the default agent's name.
        """
        if target.surface.surface_type is SurfacePlatform.SLACK:
            external_user_id = str(
                getattr(target.link, "external_user_id", "") or ""
            )
            if target.surface.config.slack.chose_pod_assistant(external_user_id):
                return True
        channel_id = str(getattr(target.link, "external_channel_id", "") or "")
        if not channel_id:
            return False
        route = target.surface.channel_route_for(
            channel_id=channel_id, channel_name=""
        )
        return bool(route is not None and route.use_pod_assistant)

    async def send_to_member(
        self,
        *,
        surface: AgentSurfaceEntity,
        user_id: UUID,
        message: str,
    ) -> bool:
        """Proactively send a message to a pod member on a specific surface.

        Powers ``surface.send`` (notifications from functions/workflows, or an
        agent reaching a specific member). Reuses the member's existing thread on
        the surface — bots can't cold-DM, so the member must have interacted
        before; returns ``False`` when no reachable thread exists.
        """
        if not surface.is_active:
            return False
        # Members of this surface's pod only, and FAIL CLOSED: this was once
        # `if self.pod_membership_port is not None`, which skipped the check
        # entirely when mis-wired — turning a wiring bug into "any user id can be
        # messaged". Not running the check is not the same as passing it.
        if self.pod_membership_port is None:
            logger.error(
                "agent_surfaces.ingress_service.send_to_member_no_membership_port.failed",
                surface_id=str(surface.id),
            )
            return False
        if surface.pod_id not in set(
            await self.pod_membership_port.get_user_pod_ids(user_id)
        ):
            return False
        external_user_repository = getattr(self, "external_user_repository", None)
        if external_user_repository is None:
            return False
        ext = await external_user_repository.get_by_resolved_user(
            platform=surface.surface_type.value, resolved_user_id=user_id
        )
        if ext is None or not ext.external_user_id:
            return False
        link = await self.conversation_link_repository.get_latest_by_surface_and_external_user(
            surface_id=surface.id, external_user_id=ext.external_user_id
        )
        if link is None:
            return False
        return await self.send_agent_message_for_conversation(
            conversation_id=link.conversation_id, message=message
        )

    async def send_agent_message_for_conversation(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        # Safety net: never deliver model reasoning/thinking tokens
        # (``<tool_call>…``) as a chat message to any surface. Some
        # OpenAI-compatible models emit these inline in the text content.
        clean_message = sanitize_user_visible_text(message)
        if not clean_message:
            return False
        message_metadata = await self._egress_metadata_with_agent_name(target, metadata)
        await target.adapter.send_message(
            credentials=target.credentials,
            event=target.event,
            message=clean_message,
            metadata=message_metadata,
        )
        return True

    async def send_display_resource_for_conversation(
        self,
        *,
        conversation_id: UUID,
        request: DisplayResourceRequest | dict[str, Any],
        tool_call_id: str | None = None,
        tool_output: object | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        display_request = (
            request
            if isinstance(request, DisplayResourceRequest)
            else DisplayResourceRequest.model_validate(request)
        )
        render_plan = build_display_resource_render_plan(
            pod_id=target.surface.pod_id,
            request=display_request,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            tool_output=tool_output,
        )
        # A FILE resource is delivered as a native attachment when it fits the
        # platform's cap; otherwise we fall through to the card+URL render plan.
        if (
            display_request.type is DisplayResourceType.FILE
            and display_request.path
            and await self._try_send_file_attachment(
                target=target,
                conversation_id=conversation_id,
                path=display_request.path,
                caption=render_plan.title,
            )
        ):
            return True
        message_metadata = await self._egress_metadata_with_agent_name(target, metadata)
        await target.adapter.send_display_resource(
            credentials=target.credentials,
            event=target.event,
            render_plan=render_plan,
            metadata=message_metadata,
        )
        return True

    async def send_questions_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
    ) -> bool:
        """Render the conversation's pending ``ask_user`` questions on its surface.

        Triggered by the WAITING run event. Reads the paused ask_user tool-call
        args, builds a render plan, and delivers it as native tappable choices
        where supported (Slack/Teams) or a formatted text message otherwise. The
        user's answer is routed back via ``handle_interaction`` (native submit) or
        the typed-reply path in ``start_agent_chat``.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        if target.surface.surface_type.is_email:
            # Email is non-interactive: never pause for a tappable/typed answer.
            logger.debug(
                "agent_surfaces.ingress_service.ask_user_suppressed_email_surface.observed",
                conversation_id=conversation_id,
            )
            return False
        pending = await self.conversation_service.get_pending_ask_user(
            conversation_id=conversation_id
        )
        if not isinstance(pending, dict):
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        raw_request = _ask_user_request_dict(pending.get("tool_args"))
        if raw_request is None:
            pending.get("tool_args")
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        try:
            request = AskUserRequest.model_validate(raw_request)
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_render_skipped.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        if not request.questions:
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        plan = build_ask_user_render_plan(
            request=request,
            conversation_id=conversation_id,
            tool_call_id=str(pending.get("tool_call_id") or tool_call_id or ""),
        )
        metadata = await self._egress_metadata_with_agent_name(target, None)
        try:
            if await target.adapter.send_questions(
                credentials=target.credentials,
                event=target.event,
                question_plan=plan,
                metadata=metadata,
            ):
                return True
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_native_render.diagnostic',
                conversation_id=conversation_id,
            )
        # Fallback: a well-formatted text message; the user replies in chat and the
        # typed-reply path in start_agent_chat resumes the run with their answer.
        # This is the guaranteed "never swallowed" path — if it ALSO fails, the
        # question reaches nobody and the run is stuck WAITING, so surface it
        # loudly and report failure to the caller (the observer logs it too).
        try:
            await target.adapter.send_message(
                credentials=target.credentials,
                event=target.event,
                message=render_questions_as_text(plan),
                metadata=metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_ask_user_text_fallback.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        return True

    async def send_approval_prompt_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
    ) -> bool:
        """Render a pending ``request_approval`` on the surface.

        Delivers native Approve/Deny buttons where supported (the tapped decision
        routes back via ``handle_interaction``); on any platform without native
        buttons, or if the native render fails, falls back to a text prompt the
        user answers "approve"/"deny" (routed back by the typed-reply path in
        ``start_agent_chat`` via ``maybe_resume_pending_interaction``). Never
        swallowed.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            logger.debug(
                'agent_surfaces.ingress_service.surface_request_approval_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        if target.surface.surface_type.is_email:
            # Email is non-interactive: never pause for an approve/deny reply.
            # (The tool now fails fast on email before pausing; this stays as a
            # defense-in-depth guard.)
            logger.debug(
                "agent_surfaces.ingress_service.request_approval_suppressed_email_surface.observed",
                conversation_id=conversation_id,
            )
            return False
        pending = await self.conversation_service.get_pending_user_interaction(
            conversation_id=conversation_id
        )
        if not isinstance(pending, dict) or pending.get("kind") != "request_approval":
            logger.debug(
                'agent_surfaces.ingress_service.surface_request_approval_not_delivered.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        tool_args = pending.get("tool_args") or {}
        # An approve-for-session button only makes sense when the paused call
        # carries a real permission gate (it lets the exact action skip future
        # prompts); otherwise it is noise.
        permission_ids = tool_args.get("permission_ids")
        allow_session = bool(isinstance(permission_ids, list) and permission_ids)
        plan = build_approval_render_plan(
            conversation_id=conversation_id,
            tool_call_id=str(pending.get("tool_call_id") or tool_call_id or ""),
            title=str(tool_args.get("title") or "Action requires your approval"),
            reason=str(tool_args.get("reason") or "") or None,
            tool_name=str(tool_args.get("tool_name") or "") or None,
            allow_session=allow_session,
        )
        metadata = await self._egress_metadata_with_agent_name(target, None)
        try:
            if await target.adapter.send_approval(
                credentials=target.credentials,
                event=target.event,
                approval_plan=plan,
                metadata=metadata,
            ):
                return True
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_request_approval_native_render.diagnostic',
                conversation_id=conversation_id,
            )
        # Fallback: a text prompt; the user replies "approve"/"deny" and the
        # typed-reply path resumes the run with their decision. If this ALSO
        # fails the approval reached nobody and the run is stuck — surface it.
        try:
            await target.adapter.send_message(
                credentials=target.credentials,
                event=target.event,
                message=plan.to_plain_text(),
                metadata=metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_request_approval_text_fallback.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        return True

    async def send_voice_note_for_conversation(
        self,
        *,
        conversation_id: UUID,
        path: str,
        caption: str | None = None,
    ) -> bool:
        """Deliver a pod audio file as a native voice note on the surface.

        Called by the ``say`` tool. Tries the platform's native voice note
        (Telegram sendVoice / audio message); falls back to a normal file
        attachment (an inline audio player on most platforms) and then a link.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        # The caption is model-authored — strip any reasoning before delivery.
        caption = sanitize_user_visible_text(caption) if caption else caption
        try:
            conversation = await self.conversation_service.conversation_repository.get_conversation(
                conversation_id
            )
            if conversation is None:
                return False
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(
                user_id=conversation.user_id,
                pod_id=target.surface.pod_id,
            )
            token = set_current_context(auth_ctx)
            try:
                file_service = build_file_service(self.uow)
                entity, content = await file_service.download_file_content_by_path(
                    target.surface.pod_id, path, auth_ctx
                )
            finally:
                reset_current_context(token)
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_voice_note_fetch_conversation.diagnostic',
                conversation_id=conversation_id,
            )
            return False

        mime = entity.mime_type or "audio/ogg"
        try:
            if await target.adapter.send_voice_note(
                credentials=target.credentials,
                event=target.event,
                file_name=entity.name,
                audio_bytes=content,
                mime=mime,
                caption=caption,
            ):
                return True
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_voice_note_send_conversation.diagnostic',
                conversation_id=conversation_id,
            )
        # Fallback: native file attachment (audio player), then a link card.
        if await self._try_send_file_attachment(
            target=target,
            conversation_id=conversation_id,
            path=path,
            caption=caption,
        ):
            return True
        return await self.send_display_resource_for_conversation(
            conversation_id=conversation_id,
            request=DisplayResourceRequest(type=DisplayResourceType.FILE, path=path),
        )

    async def _try_send_file_attachment(
        self,
        *,
        target: "_SurfaceEgressTarget",
        conversation_id: UUID,
        path: str,
        caption: str | None,
    ) -> bool:
        """Attach a pod file's bytes natively when it fits the platform cap.

        Returns True only when the file was delivered natively; on any failure
        or an oversize file returns False so the caller sends a URL link instead.
        """
        platform = target.surface.surface_type.value
        try:
            conversation = await self.conversation_service.conversation_repository.get_conversation(
                conversation_id
            )
            if conversation is None:
                return False
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(
                user_id=conversation.user_id,
                pod_id=target.surface.pod_id,
            )
            token = set_current_context(auth_ctx)
            try:
                file_service = build_file_service(self.uow)
                entity = await file_service.get_file_by_path(
                    target.surface.pod_id, path, auth_ctx
                )
                if not fits_inline(platform, entity.size_bytes):
                    return False
                _entity, content = await file_service.download_file_content_by_path(
                    target.surface.pod_id, path, auth_ctx
                )
            finally:
                reset_current_context(token)
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_native_file_attach_skipped.diagnostic',
                conversation_id=conversation_id,
            )
            return False
        return await target.adapter.send_file_attachment(
            credentials=target.credentials,
            event=target.event,
            file_name=entity.name,
            file_bytes=content,
            mime_type=entity.mime_type or "application/octet-stream",
            caption=caption,
        )

    async def try_handle_channel_setup(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Open the "who answers here?" modal, or persist what it returned.

        Returns True when the payload belonged to this flow, so the caller stops
        — none of it reaches an agent.
        """
        adapter, platform = self._adapter_for_request(request)
        if adapter is None:
            return False
        setup = await adapter.parse_channel_setup(request.payload, request.headers)
        if setup is None:
            return False

        surface = await self._surface_for_workspace(
            request, tenant_id=setup.get("tenant_id"), platform=platform
        )
        if surface is None:
            return True
        credentials = await self._resolve_credentials(surface)
        channel_id = str(setup.get("channel_id") or "")
        try:
            if setup.get("kind") == "starter_prompt":
                await adapter.send_starter_prompt(
                    credentials=credentials,
                    user_id=str(setup.get("actor_external_user_id") or ""),
                    prompt=str(setup.get("prompt") or ""),
                )
                return True

            if setup.get("kind") == "open_dm":
                agents, _ = await self.conversation_service.agent_repository.list_by_pod(
                    pod_id=surface.pod_id
                )
                await adapter.open_dm_agent_modal(
                    credentials=credentials,
                    trigger_id=str(setup.get("trigger_id") or ""),
                    agent_names=[agent.name for agent in agents],
                    current=surface.config.slack.choice_for_user(
                        setup.get("actor_external_user_id")
                    ),
                )
                return True

            if setup.get("kind") == "submit_dm":
                await self._set_dm_agent_for_user(
                    surface=surface,
                    external_user_id=str(setup.get("actor_external_user_id") or ""),
                    agent_name=setup.get("agent_name"),
                )
                await self._publish_home(
                    surface=surface,
                    adapter=adapter,
                    credentials=credentials,
                    external_user_id=str(setup.get("actor_external_user_id") or ""),
                )
                return True

            if setup.get("kind") == "open":
                agents, _ = await self.conversation_service.agent_repository.list_by_pod(
                    pod_id=surface.pod_id
                )
                await adapter.open_channel_setup_modal(
                    credentials=credentials,
                    trigger_id=str(setup.get("trigger_id") or ""),
                    channel_id=channel_id,
                    channel_label=await adapter.channel_name(
                        credentials=credentials, channel_id=channel_id
                    ),
                    agent_names=[agent.name for agent in agents],
                )
                return True

            agent_name = setup.get("agent_name")
            await self._route_channel_to_agent(
                surface=surface,
                channel_id=channel_id,
                agent_name=agent_name,
            )
            # The modal just closes on save, so without this the person has no
            # idea whether anything happened — and no way to see what they set.
            await adapter.send_channel_setup_prompt(
                credentials=credentials,
                channel_id=channel_id,
                user_id=str(setup.get("actor_external_user_id") or ""),
                confirmed_agent=agent_name or "the pod assistant",
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_channel_setup_handling.diagnostic',
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _set_dm_agent_for_user(
        self,
        *,
        surface,
        external_user_id: str,
        agent_name: str | None,
    ) -> None:
        """Record who answers one person's DMs.

        Choosing the pod assistant stores a sentinel rather than removing the
        entry — absence means "never chose", which resolves to the surface
        default agent, and that is a different answer.
        """
        if not external_user_id:
            return
        chosen = dict(surface.config.slack.dm_agent_by_user)
        chosen[external_user_id] = (
            agent_name or surface.config.slack.POD_ASSISTANT
        )
        surface.config.slack.dm_agent_by_user = chosen
        await self.surface_repository.update(surface)
        await self.uow.commit()

    async def _publish_home(
        self, *, surface, adapter, credentials, external_user_id: str
    ) -> None:
        """Render the Home tab for one viewer."""
        if not external_user_id:
            return
        agents, _ = await self.conversation_service.agent_repository.list_by_pod(
            pod_id=surface.pod_id
        )
        await adapter.publish_home_view(
            credentials=credentials,
            user_id=external_user_id,
            pod_name=None,
            dm_agent_name=(
                None
                if surface.config.slack.chose_pod_assistant(external_user_id)
                else (
                    surface.config.slack.agent_for_user(external_user_id)
                    or await self._agent_name_for_agent_id(surface.agent_id)
                )
            ),
            channel_routes=[
                (
                    route.channel_id,
                    None if route.use_pod_assistant else route.agent_name,
                )
                for route in surface.config.channels
                if route.channel_id
            ],
            agents=[(agent.name, agent.description) for agent in agents],
            apps=await self._home_apps(surface=surface, external_user_id=external_user_id),
            workspace_url=str(getattr(settings, "frontend_url", "") or "") or None,
            logo_url=surface_settings.slack_home_logo_url,
        )

    async def _home_apps(self, *, surface, external_user_id: str) -> list:
        """Apps this *viewer* may open — never the pod's full list.

        Visibility is per-user, so an unresolvable Slack identity gets no apps
        rather than everyone else's.
        """
        try:
            from app.modules.apps.contracts import list_ready_pod_apps

            external = (
                await self.external_user_repository.get_by_identity(
                    platform="SLACK",
                    tenant_id=surface.external_workspace_id,
                    external_user_id=external_user_id,
                )
                if self.external_user_repository
                else None
            )
            resolved_user_id = getattr(external, "resolved_user_id", None)
            if resolved_user_id is None:
                return []
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(user_id=resolved_user_id, pod_id=surface.pod_id)
            apps = await list_ready_pod_apps(
                uow=self.uow, pod_id=surface.pod_id, ctx=auth_ctx
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_home_apps.diagnostic',
                surface_id=str(surface.id),
            )
            return []
        domain = str(getattr(settings, "app_base_domain", "") or "").strip()
        if not domain:
            return []
        return [(app.name, f"https://{app.public_slug}.{domain}") for app in apps]

    async def _route_channel_to_agent(
        self,
        *,
        surface,
        channel_id: str,
        agent_name: str | None,
    ) -> None:
        """Point one channel at one agent, replacing any existing route.

        ``agent_name=None`` means the pod's own assistant — which is what an
        empty ``agent_name`` on a route already resolves to, so the two agree
        without a special case downstream.
        """
        if not channel_id:
            return
        routes = [
            route
            for route in surface.config.channels
            if str(route.channel_id or "") != channel_id
        ]
        routes.append(
            SurfaceChannelRoute(
                channel_id=channel_id,
                agent_name=agent_name,
                use_pod_assistant=agent_name is None,
            )
        )
        surface.config.channels = routes
        await self.surface_repository.update(surface)
        await self.uow.commit()

    def _adapter_for_request(self, request):
        if isinstance(request, SurfaceDirectWebhookIngress):
            return None, None
        platform = self._resolve_platform(request.source)
        return (self.adapter_registry.get(platform) if platform else None), platform

    async def _surface_for_workspace(self, request, *, tenant_id, platform):
        candidates = await self.surface_repository.list_active_by_type(platform)
        receiver_ids = set(getattr(request, "receiver_surface_ids", None) or [])
        if receiver_ids:
            candidates = [s for s in candidates if s.id in receiver_ids]
        if tenant_id:
            scoped = [
                surface
                for surface in candidates
                if str(surface.external_workspace_id or "") == str(tenant_id)
            ]
            if scoped:
                return scoped[0]
        return candidates[0] if len(candidates) == 1 else None

    async def try_handle_lifecycle(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Parse + route an event about the app itself rather than a message.

        Returns True when the payload was a lifecycle event, so the caller stops
        — these never become conversations. False means fall through to the
        interaction and message paths.
        """
        surface = None
        if isinstance(request, SurfaceDirectWebhookIngress):
            surface = await self.surface_repository.get(request.surface_id)
            if surface is None:
                return False
            adapter = self.adapter_registry.get(surface.surface_type)
            platform = surface.surface_type
        else:
            platform = self._resolve_platform(request.source)
            adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return False
        parsed = await adapter.parse_inbound_lifecycle(
            request.payload, request.headers
        )
        if parsed is None:
            return False

        if surface is None:
            surface = await self._surface_for_lifecycle(request, parsed, platform)
        if surface is None:
            # Nothing installed for this workspace — nothing to configure.
            return True
        try:
            await self._handle_lifecycle_event(surface=surface, parsed=parsed)
        except Exception:
            # exc_info: a swallowed TypeError here silently leaves Slack showing
            # a stale App Home, which looks exactly like "the deploy didn't work".
            logger.debug(
                'agent_surfaces.ingress_service.surface_lifecycle_handling.diagnostic',
                surface_id=str(surface.id),
                exc_info=True,
            )
        return True

    async def _surface_for_lifecycle(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
        parsed: ParsedSurfaceLifecycleEvent,
        platform,
    ):
        """The installed surface this lifecycle event belongs to.

        Matches on the workspace id the event carries, so one deployment serving
        many Slack workspaces configures the right one.
        """
        candidates = await self.surface_repository.list_active_by_type(platform)
        receiver_ids = set(
            getattr(request, "receiver_surface_ids", None) or []
        )
        if receiver_ids:
            candidates = [s for s in candidates if s.id in receiver_ids]
        if parsed.tenant_id:
            scoped = [
                surface
                for surface in candidates
                if str(surface.external_workspace_id or "") == str(parsed.tenant_id)
            ]
            if scoped:
                return scoped[0]
        return candidates[0] if len(candidates) == 1 else None

    async def _handle_lifecycle_event(
        self,
        *,
        surface,
        parsed: ParsedSurfaceLifecycleEvent,
    ) -> None:
        """React to the app's own situation changing.

        Today the one reaction is offering to configure a freshly joined
        channel. A channel that already has a route needs no prompt — the
        invite was someone re-adding the bot, not setting it up.
        """
        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return
        credentials = await self._resolve_credentials(surface)

        if parsed.kind is SurfaceLifecycleKind.HOME_OPENED:
            # Slack spins forever until a view is published, so this must answer
            # every open — including the very first, before anything is set up.
            if not parsed.actor_external_user_id:
                return
            await self._publish_home(
                surface=surface,
                adapter=adapter,
                credentials=credentials,
                external_user_id=parsed.actor_external_user_id,
            )
            return

        if parsed.kind is not SurfaceLifecycleKind.JOINED_CHANNEL:
            return
        if not parsed.actor_external_user_id or not parsed.external_channel_id:
            return
        if surface.channel_route_for(
            channel_id=parsed.external_channel_id, channel_name=""
        ):
            return
        await adapter.send_channel_setup_prompt(
            credentials=credentials,
            channel_id=parsed.external_channel_id,
            user_id=parsed.actor_external_user_id,
            channel_name=await adapter.channel_name(
                credentials=credentials, channel_id=parsed.external_channel_id
            ),
        )

    async def try_handle_interaction(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Parse + route an inbound interaction (native ask_user answer submit).

        Returns True when the payload was an interaction (handled or
        intentionally dropped); False when it is not an interaction and the
        caller should fall through to the normal message path.
        """
        surface = None
        if isinstance(request, SurfaceDirectWebhookIngress):
            surface = await self.surface_repository.get(request.surface_id)
            if surface is None:
                return False
            adapter = self.adapter_registry.get(surface.surface_type)
        else:
            platform = self._resolve_platform(request.source)
            adapter = self.adapter_registry.get(platform) if platform else None
        if adapter is None:
            return False
        parsed = await adapter.parse_inbound_interaction(
            request.payload, request.headers
        )
        if parsed is None:
            return False
        if parsed.interaction_state == "expired":
            if surface is None and isinstance(request, SurfacePlatformWebhookIngress):
                for surface_id in request.receiver_surface_ids or []:
                    surface = await self.surface_repository.get(surface_id)
                    if surface is not None:
                        break
            if surface is not None:
                credentials = await self._resolve_credentials(surface)
                await adapter.acknowledge_interaction(
                    credentials=credentials,
                    interaction=parsed,
                    text="This action expired. Please ask again.",
                    show_alert=True,
                    clear_actions=True,
                )
            return True
        await self.handle_interaction(parsed)
        return True

    async def handle_interaction(self, parsed: ParsedSurfaceInteraction) -> None:
        """Resume a paused ``ask_user`` run from a native answer submission.

        The submitted values are keyed by question header (the native render uses
        the header as each input's id), so they map straight into
        ``AskUserResponse.answers`` and resume through the approval path — the
        agent receives a proper structured answer, not a plain message. Best
        effort; never raises to the caller.
        """
        adapter = None
        credentials = None
        try:
            if parsed.action == "retry":
                tool_call_id = ""
                delivery = await resolve_current_interaction_delivery(self, parsed)
            else:
                target = parse_interaction_target(parsed)
                if target is None:
                    return
                conversation_id, tool_call_id = target
                delivery = await resolve_interaction_delivery(
                    self,
                    parsed,
                    conversation_id,
                )
            if delivery is None:
                return
            link, surface, adapter, credentials = delivery
            conversation_id = link.conversation_id

            if parsed.interaction_state == "other":
                await adapter.acknowledge_interaction(
                    credentials=credentials,
                    interaction=parsed,
                    text="Reply with your own answer.",
                )
                return

            # Replay protection: each submission is processed once. A repeat is an
            # expected double-tap, not an error — debug only.
            claimed = await self.event_dedup_store.claim_message(
                surface_installation_id=surface.id,
                platform=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_message_id=parsed.dedup_id,
            )
            if not claimed:
                logger.debug(
                    "agent_surfaces.ingress_service.surface_interaction_ignored_replay_duplicate.observed",
                    conversation_id=conversation_id,
                    dedup_id=parsed.dedup_id,
                )
                return

            # Authz: only the surface user who owns the conversation may submit
            # the answer that was shown to them.
            if not interaction_sender_matches(link, parsed):
                logger.debug(
                    'agent_surfaces.ingress_service.surface_answer_submission_rejected_submitter.diagnostic',
                    external_user_id=parsed.external_user_id,
                    conversation_id=conversation_id,
                )
                return

            conversation = await self.conversation_service.conversation_repository.get_conversation(
                conversation_id
            )
            if conversation is None:
                logger.debug(
                    'agent_surfaces.ingress_service.surface_interaction_dropped_conversation_not.diagnostic',
                    conversation_id=conversation_id,
                )
                return

            if parsed.action == "retry":
                refreshed = await self._refresh_interaction_conversation(
                    link=link,
                    surface=surface,
                    conversation=conversation,
                )
                if refreshed is None:
                    return
                link, conversation, restarted = refreshed
                if restarted:
                    await adapter.acknowledge_interaction(
                        credentials=credentials,
                        interaction=parsed,
                        text="This chat started a new conversation. Send your message again.",
                        show_alert=True,
                        clear_actions=True,
                    )
                    return
                await retry_interaction_conversation(
                    conversation_service=self.conversation_service,
                    uow=self.uow,
                    conversation=conversation,
                )
                await adapter.acknowledge_interaction(
                    credentials=credentials,
                    interaction=parsed,
                    text="Retrying…",
                    clear_actions=True,
                )
                return

            # An approval button carries an explicit decision (approve / deny /
            # approve-for-session) with no answer payload; an ask_user submit
            # carries answers keyed by question header.
            if parsed.approval_decision is not None:
                decision = AgentRunApprovalDecision(parsed.approval_decision)
                response: dict[str, object] = {}
            else:
                decision = AgentRunApprovalDecision.APPROVE_ONCE
                response = {"answers": merge_other_answers(parsed.values)}
            auth_ctx = await create_authorization_data_service(
                self.uow
            ).build_user_context(
                user_id=conversation.user_id,
                pod_id=conversation.pod_id,
            )
            token = set_current_context(auth_ctx)
            try:
                await self.conversation_service.resolve_user_approval_internal(
                    conversation=conversation,
                    approval_id=tool_call_id,
                    user_id=conversation.user_id,
                    pod_id=conversation.pod_id,
                    decision=decision,
                    response=response,
                    # Same webhook deadline as the typed-reply path above.
                    defer_reconciliation=True,
                )
            finally:
                reset_current_context(token)
            await adapter.acknowledge_interaction(
                credentials=credentials,
                interaction=parsed,
                text="Done",
                clear_actions=True,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_interaction_handling_s.diagnostic'
            )
            if adapter is not None and credentials is not None:
                await adapter.acknowledge_interaction(
                    credentials=credentials,
                    interaction=parsed,
                    text="I couldn’t complete that action.",
                    show_alert=True,
                )

    async def _refresh_interaction_conversation(
        self,
        *,
        link: AgentSurfaceConversationLink,
        surface: AgentSurfaceEntity,
        conversation,
    ) -> tuple[AgentSurfaceConversationLink, Any, bool] | None:
        """Apply the normal DM agent/TTL reset policy before an action runs."""

        try:
            last_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except (TypeError, ValueError):
            return link, conversation, False
        route = await self._resolve_route(surface=surface, parsed=last_event)
        if route is None:
            return link, conversation, False
        refreshed_link, _ = await self._get_or_create_conversation_link(
            surface=surface,
            parsed=last_event,
            resolved_user=ResolvedSurfaceUser(
                internal_user_id=conversation.user_id,
                external_user_id=link.external_user_id,
            ),
            route=route,
            current_conversation_agent_id=conversation.agent_id,
        )
        if refreshed_link.conversation_id == link.conversation_id:
            return refreshed_link, conversation, False
        refreshed_conversation = (
            await self.conversation_service.conversation_repository.get_conversation(
                refreshed_link.conversation_id
            )
        )
        if refreshed_conversation is None:
            return None
        return refreshed_link, refreshed_conversation, True

    async def send_processing_indicator_for_conversation(
        self,
        *,
        conversation_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        indicator_metadata = await self._egress_metadata_with_agent_name(
            target, metadata
        )
        await target.adapter.add_processing_indicator(
            credentials=target.credentials,
            event=target.event,
            metadata=indicator_metadata,
        )
        return True

    async def send_progress_update_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Stream a live progress line on platforms with editable messages.

        Best-effort: returns the (possibly updated) handle and never raises, so a
        failed progress edit cannot affect the agent run.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return progress_handle
        try:
            # Author the stream as the agent: the answer that closes this same
            # message carries the agent's name, so the stream must too or the
            # thread reads as two different speakers.
            metadata = await self._egress_metadata_with_agent_name(target, None)
            return await target.adapter.stream_progress(
                credentials=target.credentials,
                event=target.event,
                progress_text=progress_text,
                progress_handle=progress_handle,
                metadata=metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_update_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return progress_handle

    async def append_stream_text_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None,
        text: str,
    ) -> dict[str, Any] | None:
        """Append streamed model text; returns the (possibly new) handle.

        Best-effort by construction — a dropped delta must never take down a
        run, and the final answer still lands through the normal path.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return progress_handle
        try:
            metadata = await self._egress_metadata_with_agent_name(target, None)
            return await target.adapter.append_stream_text(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
                text=text,
                metadata=metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_stream_text_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return progress_handle

    async def finish_progress_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
        already_streamed: bool = False,
    ) -> bool:
        """Close a live progress stream with the final answer, as one message.

        Returns False when the platform cannot do this (every platform except
        Slack today) or the attempt failed, so the caller falls back to clearing
        progress and sending the answer separately.
        """
        if not progress_handle:
            return False
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        clean_message = sanitize_user_visible_text(message)
        # An already-streamed answer legitimately has nothing left to send — the
        # stream still has to be closed, or it spins forever.
        if not clean_message and not already_streamed:
            return False
        message_metadata = await self._egress_metadata_with_agent_name(target, metadata)
        try:
            return await target.adapter.finish_progress(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
                message=clean_message,
                metadata=message_metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_finish_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return False

    async def clear_progress_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Remove the streaming progress message at run end (best-effort)."""
        if not progress_handle:
            return
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return
        try:
            await target.adapter.end_progress(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_clear_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )

    async def _match_surface_for_user(
        self,
        surfaces: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
    ) -> AgentSurfaceEntity | None:
        """Return the first surface whose pod the resolved user is a member of."""
        if resolved_user is None or resolved_user.internal_user_id is None:
            return None

        if not self.pod_membership_port:
            return None

        user_pod_ids = set(
            await self.pod_membership_port.get_user_pod_ids(
                resolved_user.internal_user_id
            )
        )
        for surface in surfaces:
            if surface.pod_id in user_pod_ids:
                return surface
        return None

    async def _select_surface(
        self,
        *,
        candidates: list[AgentSurfaceEntity],
        resolved_user: ResolvedSurfaceUser | None,
        parsed: ParsedInboundSurfaceEvent,
        platform: str,
    ) -> AgentSurfaceEntity | None:
        """Pick which candidate surface an inbound event belongs to.

        Deterministic precedence — this is what makes a sender reachable via a
        shared system bot/number across pods in multiple orgs route consistently:

        1. **Pod membership** — only surfaces whose pod the sender belongs to are
           eligible.
        2. **User default (authoritative)** — a valid saved
           ``users.preferences.default_surfaces[platform]`` wins over everything
           else, including an existing conversation on another pod, so changing
           the default re-routes new messages to the chosen pod (starting a fresh
           conversation there). A *stale* default (pointing at a pod the user left)
           is cleared and ignored.
        3. **Continuity** — otherwise reuse the surface an existing conversation
           for this exact chat already lives on, so a returning chat doesn't bounce
           between pods.
        4. **Deterministic tiebreak** — the first member candidate (``candidates``
           is ordered by ``created_at, id``).

        For an unresolved sender (or one who belongs to no candidate pod), fall
        back to continuity alone. Membership is still re-validated downstream in
        ``_prepare_surface_context`` (which sends the pod-access/signup reply when
        appropriate), so this only decides *which* candidate — never bypasses the
        access check.
        """
        # Resolve continuity once — it is both a fallback for unresolved senders
        # and the tie-decider when no valid default is set.
        continuity_id = (
            await self.conversation_link_repository.find_surface_id_for_external_thread(
                platform=platform,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_user_id=parsed.sender_external_user_id,
            )
        )
        continuity_surface = (
            next((s for s in candidates if s.id == continuity_id), None)
            if continuity_id is not None
            else None
        )

        # Unresolved / no-membership-port senders: continuity is all we have.
        if (
            resolved_user is None
            or resolved_user.internal_user_id is None
            or not self.pod_membership_port
        ):
            return continuity_surface

        user_id = resolved_user.internal_user_id
        user_pod_ids = set(await self.pod_membership_port.get_user_pod_ids(user_id))
        member_candidates = [s for s in candidates if s.pod_id in user_pod_ids]
        if not member_candidates:
            # No pod the user belongs to; keep continuity if any (membership is
            # re-validated downstream), else nothing to route to.
            return continuity_surface

        member_by_id = {s.id: s for s in member_candidates}

        # 2. A valid saved default is authoritative — it wins over continuity.
        get_default = getattr(
            self.pod_membership_port, "get_user_default_surface_id", None
        )
        if get_default is not None:
            default_id = await get_default(user_id, platform)
            if default_id is not None:
                if default_id in member_by_id:
                    return member_by_id[default_id]
                # Stale default: it points at a surface the user is no longer a
                # member of. Clear it so routing stops silently honoring it.
                logger.debug(
                    'agent_surfaces.ingress_service.agent_surface_default_user_s.diagnostic',
                    user_id=user_id,
                    default_id=default_id,
                )
                clear_default = getattr(
                    self.pod_membership_port, "clear_user_default_surface_id", None
                )
                if clear_default is not None:
                    try:
                        await clear_default(user_id, platform)
                    except Exception:
                        logger.debug(
                            'agent_surfaces.ingress_service.clear_stale_surface_default_user.diagnostic',
                            user_id=user_id,
                        )

        # 3. Continuity — reuse the surface this chat already lives on (only when
        # it is a pod the user still belongs to).
        if continuity_surface is not None and continuity_surface.id in member_by_id:
            return continuity_surface

        if len(member_candidates) == 1:
            return member_candidates[0]

        # 4. Deterministic tiebreak (candidates are ordered by created_at, id).
        # The user can pick a default via GET/PUT /surfaces/me when this happens.
        return member_candidates[0]

    async def _telegram_text_mention_enrich(
        self,
        parsed: ParsedInboundSurfaceEvent,
        surface: AgentSurfaceEntity,
    ) -> ParsedInboundSurfaceEvent:
        """Upgrade mentioned_agent when a Telegram group message actually
        targets this bot.

        The parser records @username / text_mention entities without claiming
        they mention the bot (a `mention` entity is just a plain @username and
        doesn't identify the user). Here we resolve the bot's @username and
        numeric user id via getMe and check:

        - whether ``@{bot_username}`` appears in the mention entities (precise),
        - whether the bot's user id is in the text_mention entities (precise),
        - whether ``@{bot_username}`` appears in the message text (fallback for
          a manually typed name that produced no entity).

        Best-effort; returns the event unchanged on any failure."""
        try:
            from app.modules.agent_surfaces.platforms.telegram.service import (
                TelegramPlatformService,
            )

            credentials = await self._resolve_credentials(surface)
            service = TelegramPlatformService(credentials)
            bot_username = (await service.get_bot_username() or "").lower()
            bot_user_id = await service.get_bot_user_id()
            metadata = parsed.metadata or {}
            mentioned_usernames = {
                str(name).lower() for name in metadata.get("mentioned_usernames") or []
            }
            text_mention_user_ids = {
                str(uid) for uid in metadata.get("text_mention_user_ids") or []
            }
            text = (parsed.message_text or "").lower()

            matched = False
            if bot_username and bot_username in mentioned_usernames:
                matched = True
            elif bot_user_id and bot_user_id in text_mention_user_ids:
                matched = True
            elif bot_username and f"@{bot_username}" in text:
                matched = True

            if matched:
                return parsed.model_copy(update={"mentioned_agent": True})
        except Exception:
            logger.debug(
                "agent_surfaces.ingress_service.telegram_text_mention_enrich_s.observed"
            )
        return parsed

    async def _resolve_credentials(
        self,
        surface: AgentSurfaceEntity,
    ) -> dict[str, Any]:
        return await self.credential_resolver.for_surface(surface)

    async def _resolve_credentials_from_context(
        self, context: AgentSurfaceContext
    ) -> dict[str, Any]:
        if self._uow_factory is not None:
            # Worker path: read credentials in a short UoW and return the plain
            # dict, so no connection is held during the platform I/O that follows.
            async with self._uow_factory() as uow:
                resolver = SurfaceCredentialResolver(
                    session=uow.session,
                    connector_service=self._connector_service_factory(uow),
                )
                return await resolver.for_platform(
                    context.platform,
                    context.surface_account_id,
                )
        return await self.credential_resolver.for_platform(
            context.platform,
            context.surface_account_id,
        )

    def _resolve_platform(self, source: str) -> str | None:
        platform = SurfacePlatform.from_source(source)
        return platform.value if platform else None

    @staticmethod
    def _scoped_fallback_surface(
        request: SurfacePlatformWebhookIngress,
        surfaces: list[AgentSurfaceEntity],
    ) -> AgentSurfaceEntity | None:
        if request.receiver_surface_ids and surfaces:
            return surfaces[0]
        return None

    async def _prepare_unrouted_platform_context(
        self,
        *,
        platform: str,
        surface: AgentSurfaceEntity | None,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        resolved_user: ResolvedSurfaceUser | None = None,
    ) -> SurfaceReplyContext | None:
        if not parsed.is_dm:
            return None
        if surface is not None:
            if self._is_self_email_event(surface=surface, parsed=parsed):
                return None
            if surface.should_ignore_sender(parsed.sender_external_user_id):
                return None
        if resolved_user is None:
            credentials = (
                await self._resolve_credentials(surface)
                if surface is not None
                else await self.credential_resolver.for_platform(platform, None)
            )
            resolved_user = await self._resolve_sender_identity(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
            )
        agent_display_name = (
            (await self._agent_name_for_surface(surface)) if surface else None
        ) or "Lemma"
        return await prepare_unrouted_context(
            platform=platform,
            surface=surface,
            parsed=parsed,
            adapter=adapter,
            resolved_user=resolved_user,
            agent_display_name=agent_display_name,
            event_dedup_store=self.event_dedup_store,
        )

    async def _prepare_surface_context(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        adapter: SurfacePlatformAdapterPort,
        resolved_user: ResolvedSurfaceUser | None = None,
    ) -> AgentSurfaceContext | None:
        if self._is_self_email_event(surface=surface, parsed=parsed):
            return None

        if surface.should_ignore_sender(parsed.sender_external_user_id):
            return None

        claimed = await self.event_dedup_store.claim_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        )
        if not claimed:
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_ignored_duplicate_external.observed",
                surface_type=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
            )
            return None

        credentials = await self._resolve_credentials(surface)
        fallback_agent_name = await self._agent_name_for_surface(surface)
        fallback_agent_display_name = fallback_agent_name or "Lemma"

        try:
            enriched = await adapter.enrich_inbound_event(
                credentials=credentials,
                event=parsed,
            )
            if enriched is None:
                logger.debug(
                    "agent_surfaces.ingress_service.agent_surface_dropped_event_after.observed",
                    surface_type=surface.surface_type,
                )
                return None
            parsed = enriched
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.enriching_inbound_event_s_message.diagnostic',
                surface_type=surface.surface_type,
            )

        # Re-check after enrichment: email triggers (e.g. Outlook) deliver a
        # minimal payload with no sender, so the pre-enrich self-check above
        # cannot see it. Without this the surface would process its own
        # outgoing replies and loop, re-sending the signup/agent reply forever.
        if self._is_self_email_event(surface=surface, parsed=parsed):
            return None

        attachment_count = len(parsed.metadata.get("attachments") or [])
        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_prepared_inbound_event.observed",
            surface_type=surface.surface_type,
            attachment_count=attachment_count,
        )

        if resolved_user is None:
            resolved_user = await self._resolve_sender_identity(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
            )
        if resolved_user.internal_user_id is None:
            return unresolved_sender_context(
                surface=surface,
                parsed=parsed,
                adapter=adapter,
                agent_display_name=fallback_agent_display_name,
            )
        confirmation = adapter.linked_sender_confirmation(parsed)
        if confirmation is not None:
            return identity_confirmation_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
                confirmation=confirmation,
            )
        if (
            await self._match_surface_for_user(
                surfaces=[surface],
                resolved_user=resolved_user,
            )
            is None
        ):
            logger.debug(
                "agent_surfaces.ingress_service.agent_surface_resolved_user_not.observed",
                surface_type=surface.surface_type,
                internal_user_id=resolved_user.internal_user_id,
                pod_id=surface.pod_id,
            )
            return nonmember_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )
        if not surface.config.identity.allows_email(resolved_user.email):
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )

        route = await self._resolve_route(surface=surface, parsed=parsed)
        if route is None:
            return surface_setup_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
            )

        link, created_conversation_title = await self._get_or_create_conversation_link(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            route=route,
        )

        return SurfaceChatContext(
            created_conversation_title=created_conversation_title,
            platform=surface.surface_type,
            pod_id=surface.pod_id,
            agent_name=route.agent_name,
            conversation_id=link.conversation_id,
            user_id=resolved_user.internal_user_id,
            surface_id=surface.id,
            surface_name=surface.name,
            surface_account_id=surface.account_id,
            surface_config=surface.config,
            agent_display_name=route.agent_display_name,
            message_text=parsed.message_text,
            message_metadata=SurfaceMessageMetadata(
                surface_platform=surface.surface_type,
                sender_display_name=resolved_user.display_name,
                sender_email=resolved_user.email,
                sender_phone=resolved_user.phone,
                event_metadata=parsed.metadata,
            ),
            message_user_id=resolved_user.internal_user_id,
            message_external_user_id=resolved_user.external_user_id,
            message_external_message_id=parsed.external_message_id,
            event=parsed,
        )

    async def _resolve_route_agent(
        self,
        *,
        surface: AgentSurfaceEntity,
        route: SurfaceChannelRoute,
    ) -> tuple[UUID | None, str | None]:
        """Resolve a route's agent name to (id, name); a renamed or deleted
        route agent falls back to the surface default agent."""
        # The pod assistant is the *absence* of an agent, so it must short
        # circuit before the surface-default fallback below — otherwise picking
        # it silently routes to whichever agent the surface defaults to.
        if route.use_pod_assistant:
            return None, None
        if route.agent_name:
            agent = (
                await self.conversation_service.agent_repository.get_by_pod_and_name(
                    pod_id=surface.pod_id,
                    name=route.agent_name,
                )
            )
            if agent is not None:
                return agent.id, agent.name
            logger.debug(
                'agent_surfaces.ingress_service.surface_channel_route_agent_s.diagnostic',
                pod_id=surface.pod_id,
            )
        agent_id = surface.agent_id
        return agent_id, await self._agent_name_for_agent_id(agent_id)

    async def _agent_name_for_surface(
        self,
        surface: AgentSurfaceEntity,
    ) -> str | None:
        return await self._agent_name_for_agent_id(surface.agent_id)

    async def _agent_name_for_agent_id(
        self,
        agent_id: UUID | None,
    ) -> str | None:
        if agent_id is None:
            return None
        agent = await self.conversation_service.agent_repository.get(agent_id)
        return agent.name if agent else None

    async def _resolve_route(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> ResolvedSurfaceRoute | None:
        if parsed.is_dm or surface.mode is SurfaceMode.EMAIL:
            agent_id = surface.agent_id
            agent_name = await self._agent_name_for_agent_id(agent_id)
            # On Slack a person can choose which agent answers their own DMs.
            # Their choice wins over the workspace default; everyone who has
            # not chosen keeps it, so this is purely additive.
            if surface.surface_type is SurfacePlatform.SLACK:
                # An explicit pod-assistant pick means *no* agent, which is not
                # the same as falling back to the surface default.
                if surface.config.slack.chose_pod_assistant(
                    parsed.sender_external_user_id
                ):
                    return ResolvedSurfaceRoute(
                        agent_id=None,
                        agent_name=None,
                        agent_display_name="Lemma",
                        conversation_kind="DM",
                        route_key="dm",
                    )
                chosen = surface.config.slack.agent_for_user(
                    parsed.sender_external_user_id
                )
                if chosen:
                    agent = await self.conversation_service.agent_repository.get_by_pod_and_name(
                        pod_id=surface.pod_id,
                        name=chosen,
                    )
                    if agent is not None:
                        agent_id, agent_name = agent.id, agent.name
                    else:
                        # A renamed or deleted agent must not strand the person
                        # with a dead DM — fall back to the surface default.
                        logger.debug(
                            'agent_surfaces.ingress_service.surface_dm_agent_choice_missing.diagnostic',
                            pod_id=surface.pod_id,
                        )
            return ResolvedSurfaceRoute(
                agent_id=agent_id,
                agent_name=agent_name,
                agent_display_name=agent_name or "Lemma",
                conversation_kind="EMAIL"
                if surface.mode is SurfaceMode.EMAIL
                else "DM",
                route_key="email" if surface.mode is SurfaceMode.EMAIL else "dm",
            )

        # Telegram groups: the bot replies when @mentioned (or in a reply within
        # its own thread). Being added to the group by an admin is the
        # authorization, so there is no per-group route config — route to the
        # surface's default agent. The sender is still resolved + pod-membership
        # checked upstream, so only pod members can invoke it.
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            if not (parsed.mentioned_agent or parsed.metadata.get("is_thread_reply")):
                return None
            agent_id = surface.agent_id
            agent_name = await self._agent_name_for_agent_id(agent_id)
            return ResolvedSurfaceRoute(
                agent_id=agent_id,
                agent_name=agent_name,
                agent_display_name=agent_name or "Lemma",
                conversation_kind="CHANNEL",
                route_key=f"channel:{parsed.external_channel_id}",
            )

        if surface.surface_type not in {SurfacePlatform.SLACK, SurfacePlatform.TEAMS}:
            return None

        route = surface.channel_route_for(
            channel_id=parsed.external_channel_id,
            channel_name=parsed.metadata.get("channel_name"),
        )
        if (
            route is None
            and surface.external_channel_id
            and surface.external_channel_id == parsed.external_channel_id
        ):
            # Surface bound directly to one channel without explicit routes.
            route = SurfaceChannelRoute(channel_id=surface.external_channel_id)
        if route is None:
            return None

        # Channels always require an @mention (or a reply within a bot thread);
        # there is no per-route opt-out.
        if not (parsed.mentioned_agent or parsed.metadata.get("is_thread_reply")):
            return None

        agent_id, agent_name = await self._resolve_route_agent(
            surface=surface, route=route
        )
        route_key = (
            f"channel:{parsed.external_channel_id}"
            if parsed.external_channel_id
            else f"channel-name:{route.channel_name}"
        )
        return ResolvedSurfaceRoute(
            agent_id=agent_id,
            agent_name=agent_name,
            agent_display_name=agent_name or "Lemma",
            conversation_kind="CHANNEL",
            route_key=route_key,
        )

    async def _get_or_create_conversation_link(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        route: ResolvedSurfaceRoute,
        current_conversation_agent_id: UUID | None = None,
    ) -> tuple[AgentSurfaceConversationLink, str | None]:
        """Return the link, plus the new conversation's title when one was created.

        The title is how a caller learns a *fresh* conversation started on this
        turn — which is the only moment worth naming the thread on the platform.
        None means the link already existed.
        """
        external_user_id = resolved_user.external_user_id
        link = await self.conversation_link_repository.get_by_external_thread(
            surface_id=surface.id,
            platform=surface.surface_type.value,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_user_id=external_user_id,
        )
        event_payload = parsed.model_dump(mode="json")
        if link is not None:
            if self._should_reset_dm_conversation(
                surface=surface,
                link=link,
                route=route,
                current_conversation_agent_id=current_conversation_agent_id,
            ):
                conversation = await self._create_surface_conversation(
                    surface=surface,
                    parsed=parsed,
                    resolved_user=resolved_user,
                    external_user_id=external_user_id,
                    route=route,
                )
                updated = await self.conversation_link_repository.update_conversation(
                    link_id=link.id,
                    conversation_id=conversation.id,
                    last_event=event_payload,
                    last_message_id=parsed.external_message_id,
                    routed_agent_id=route.agent_id,
                    conversation_kind=route.conversation_kind,
                    route_key=route.route_key,
                )
                return (updated or link), conversation.title
            updated = await self.conversation_link_repository.update_last_event(
                link_id=link.id,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
            )
            await self._update_conversation_surface_metadata(
                conversation_id=link.conversation_id,
                surface=surface,
                parsed=parsed,
                external_user_id=external_user_id,
                route_key=link.route_key or route.route_key,
                routed_agent_id=link.routed_agent_id or route.agent_id,
                conversation_kind=link.conversation_kind or route.conversation_kind,
            )
            return (updated or link), None

        conversation = await self._create_surface_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            external_user_id=external_user_id,
            route=route,
        )
        created_link = await self.conversation_link_repository.create(
            AgentSurfaceConversationLink(
                surface_id=surface.id,
                conversation_id=conversation.id,
                platform=surface.surface_type.value,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_user_id=external_user_id,
                routed_agent_id=route.agent_id,
                conversation_kind=route.conversation_kind,
                route_key=route.route_key,
                last_event=event_payload,
                last_message_id=parsed.external_message_id,
                # This row exists because they just wrote to us.
                last_inbound_at=datetime.now(timezone.utc),
            )
        )
        return created_link, conversation.title

    def _should_reset_dm_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        link: AgentSurfaceConversationLink,
        route: ResolvedSurfaceRoute | None = None,
        current_conversation_agent_id: UUID | None = None,
    ) -> bool:
        if surface.mode is not SurfaceMode.DM:
            return False
        if (
            route is not None
            and current_conversation_agent_id is not None
            and current_conversation_agent_id != route.agent_id
        ):
            return True
        if route is not None and link.routed_agent_id != route.agent_id:
            return True
        reset_hours = surface.config.dm_conversation_reset_after_hours
        if reset_hours <= 0:
            return False
        # Inbound activity, NOT ``updated_at``: an outbound notification also
        # writes this row, so keying the reset off ``updated_at`` would let a
        # proactive message suppress it and leak yesterday's context into today.
        last_seen = link.inbound_activity_at
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - last_seen > timedelta(hours=reset_hours)

    async def _create_surface_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        resolved_user: ResolvedSurfaceUser,
        external_user_id: str | None,
        route: ResolvedSurfaceRoute,
    ):
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=resolved_user.internal_user_id,
            pod_id=surface.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            return await self.conversation_service.create_conversation(
                pod_id=surface.pod_id,
                agent_name=route.agent_name,
                user_id=resolved_user.internal_user_id,
                title=self._surface_conversation_title(
                    parsed,
                    fallback=f"{surface.surface_type.value} Conversation",
                ),
                metadata={
                    "source": "agent_surfaces",
                    "surface_id": str(surface.id),
                    "surface_platform": surface.surface_type.value,
                    "external_channel_id": parsed.external_channel_id,
                    "external_thread_id": parsed.external_thread_id,
                    "external_user_id": external_user_id,
                    "external_message_id": parsed.external_message_id,
                    "route_key": route.route_key,
                    "conversation_kind": route.conversation_kind,
                    "routed_agent_id": str(route.agent_id) if route.agent_id else None,
                    "agent_display_name": route.agent_display_name,
                    "surface_event_metadata": (
                        surface_event_metadata.model_dump(mode="json")
                        if surface_event_metadata
                        else None
                    ),
                },
            )
        finally:
            reset_current_context(token)

    async def _update_conversation_surface_metadata(
        self,
        *,
        conversation_id: UUID,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        external_user_id: str | None,
        route_key: str | None = None,
        routed_agent_id: UUID | None = None,
        conversation_kind: str | None = None,
    ) -> None:
        surface_event_metadata = build_surface_event_metadata(
            surface.surface_type.value,
            parsed.metadata,
        )
        updates = {
            "source": "agent_surfaces",
            "surface_id": str(surface.id),
            "surface_platform": surface.surface_type.value,
            "external_channel_id": parsed.external_channel_id,
            "external_thread_id": parsed.external_thread_id,
            "external_user_id": external_user_id,
            "external_message_id": parsed.external_message_id,
            "route_key": route_key,
            "conversation_kind": conversation_kind,
            "routed_agent_id": str(routed_agent_id) if routed_agent_id else None,
            "agent_display_name": await self._agent_name_for_surface(surface)
            or "Lemma",
            "surface_event_metadata": (
                surface_event_metadata.model_dump(mode="json")
                if surface_event_metadata
                else None
            ),
        }
        await self.surface_repository.merge_conversation_metadata(
            conversation_id, updates
        )

    async def _resolve_sender_identity(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        parsed: ParsedInboundSurfaceEvent,
        credentials: dict[str, Any],
    ) -> ResolvedSurfaceUser:
        try:
            sender_profile = await adapter.fetch_sender_profile(
                credentials=credentials,
                event=parsed,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.fetching_sender_profile_s_s.diagnostic'
            )
            sender_profile = None
        resolved = await self.identity_service.resolve(
            event=parsed,
            sender_profile=sender_profile,
        )
        return await self._hydrate_resolved_user(resolved)

    async def _hydrate_resolved_user(
        self,
        resolved_user: ResolvedSurfaceUser,
    ) -> ResolvedSurfaceUser:
        if (
            resolved_user.internal_user_id is not None
            and self.pod_membership_port is not None
            and not resolved_user.email
        ):
            resolved_user.email = await self.pod_membership_port.get_user_email(
                resolved_user.internal_user_id
            )
        return resolved_user

    def _is_self_email_event(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
    ) -> bool:
        if not surface.surface_type.is_email:
            return False
        surface_email = str(surface.surface_identity_email or "").strip().lower()
        sender_email = (
            str(parsed.sender_email or parsed.sender_external_user_id or "")
            .strip()
            .lower()
        )
        return bool(surface_email and sender_email and surface_email == sender_email)

    def _surface_conversation_title(
        self,
        parsed: ParsedInboundSurfaceEvent,
        *,
        fallback: str,
    ) -> str:
        title = " ".join((parsed.message_text or "").split())
        if not title:
            return fallback
        if len(title) <= _CONVERSATION_TITLE_MAX_LENGTH:
            return title
        return f"{title[: _CONVERSATION_TITLE_MAX_LENGTH - 3].rstrip()}..."


