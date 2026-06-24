from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.config import settings
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.tools.user_interaction.models import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.platforms.attachment_limits import fits_inline
from app.modules.agent_surfaces.platforms.rendering import strip_thinking_tokens
from app.modules.datastore.api.dependencies import build_file_service
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ResolvedSurfaceUser,
    SurfaceChannelRoute,
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
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
    SurfaceFileIngestService,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_ask_user_render_plan,
    build_display_resource_render_plan,
    merge_other_answers,
    parse_callback_id,
    render_questions_as_text,
)
from app.modules.connectors.services.connector_service import ConnectorService
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
        uow,
        surface_repository: SurfaceInstallationRepositoryPort,
        conversation_link_repository: SurfaceConversationLinkRepository,
        conversation_service: ConversationService,
        connector_service: ConnectorService,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
        pod_membership_port: SurfacePodMembershipPort | None = None,
        file_ingest_service: SurfaceFileIngestService | None = None,
    ):
        self.uow = uow
        self.surface_repository = surface_repository
        self.conversation_link_repository = conversation_link_repository
        self.conversation_service = conversation_service
        self.connector_service = connector_service
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.file_ingest_service = file_ingest_service or SurfaceFileIngestService(
            adapter_registry=self.adapter_registry
        )
        self.external_user_repository = ExternalSurfaceUserRepository(uow)
        self.event_dedup_store = event_dedup_store or get_surface_event_dedup_store()
        self.identity_service = SurfaceIdentityResolutionService(
            uow, self.external_user_repository
        )
        self.pod_membership_port = pod_membership_port
        self.credential_resolver = SurfaceCredentialResolver(
            session=uow.session,
            connector_service=connector_service,
        )

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
            logger.info(
                "Agent surface ignored webhook because payload did not parse "
                "source=%s payload_type=%s",
                request.source,
                (request.payload or {}).get("type"),
            )
            return None

        surfaces = await self.surface_repository.list_active_by_type(platform)

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
            logger.info(
                "Agent surface ignored webhook with no matching surface platform=%s "
                "tenant=%s active_surfaces=%d is_dm=%s mentioned_agent=%s "
                "is_thread_reply=%s channel=%s",
                platform,
                parsed.tenant_id,
                len(surfaces),
                parsed.is_dm,
                parsed.mentioned_agent,
                bool((parsed.metadata or {}).get("is_thread_reply")),
                parsed.external_channel_id,
            )
            return None

        # Resolve the sender once (using the first candidate's credentials) and
        # pick the candidate whose pod the sender belongs to. An unknown sender
        # only proceeds when the target surface is unambiguous — it gets the
        # signup/link flow on that surface.
        identity_surface = candidates[0]
        resolved_user = await self._resolve_sender_identity(
            adapter=adapter,
            parsed=parsed,
            credentials=await self._resolve_credentials(identity_surface),
        )
        matched_surface = await self._match_surface_for_user(
            surfaces=candidates,
            resolved_user=resolved_user,
        )
        if (
            matched_surface is None
            and len(candidates) == 1
            and resolved_user.internal_user_id is None
        ):
            matched_surface = identity_surface

        if matched_surface is None:
            logger.info(
                "Agent surface ignored webhook with no matching pod surface platform=%s tenant=%s",
                platform,
                parsed.tenant_id,
            )
            return None

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
            logger.info(
                "Surface webhook received for unknown surface_id=%s",
                request.surface_id,
            )
            return None

        if not surface.is_active or not surface.status.accepts_inbound_events():
            logger.info(
                "Surface webhook received for inactive surface_id=%s",
                request.surface_id,
            )
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

        credentials = await self._resolve_credentials_from_context(parsed_context)
        event = parsed_context.event

        raw_event_metadata = (
            parsed_context.message_metadata.event_metadata
            if isinstance(parsed_context, SurfaceChatContext)
            else {}
        )
        logger.info(
            "Agent surface executing chat platform=%s conversation=%s attachments=%d",
            platform,
            getattr(parsed_context, "conversation_id", None),
            len((raw_event_metadata.get("attachments") or [])),
        )

        if isinstance(parsed_context, SurfaceReplyContext):
            try:
                reply_metadata = dict(
                    getattr(parsed_context, "reply_metadata", {}) or {}
                )
                await adapter.send_message(
                    credentials=credentials,
                    event=event,
                    message=parsed_context.reply_message or self._signup_message(),
                    metadata={
                        "agent_display_name": parsed_context.agent_display_name,
                        **reply_metadata,
                    },
                )
            except Exception as exc:
                logger.error(
                    "Failed sending signup surface reply: %s", exc, exc_info=True
                )
            return

        await self.start_agent_chat(parsed_context)

    async def start_agent_chat(self, context: SurfaceChatContext) -> None:
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        credentials = await self._resolve_credentials_from_context(context)
        try:
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )
        except Exception as exc:
            logger.warning("Failed adding surface processing indicator: %s", exc)

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
            except Exception as exc:
                logger.warning("Surface file auto-ingest failed: %s", exc)

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
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=context.user_id,
            pod_id=context.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            # If the run is paused on an ask_user, treat this inbound text as the
            # answer and resume — rather than starting a new message/run. This is
            # how the formatted-text fallback (and any "type your own" reply) gets
            # back into the run as a structured answer.
            if not await self._maybe_resume_pending_interaction(context, message_text):
                await self.conversation_service.add_user_message_and_start_run(
                    conversation_id=context.conversation_id,
                    user_id=context.user_id,
                    content=message_text,
                    pod_id=context.pod_id,
                    agent_name=context.agent_name,
                    message_metadata=metadata,
                )
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
        except Exception as exc:
            logger.warning(
                "Surface channel-context fetch failed platform=%s conversation=%s error=%s",
                context.platform,
                context.conversation_id,
                exc,
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
                from app.modules.agent.tools.speech.provider import get_speech_provider

                provider = get_speech_provider()
            except Exception as exc:
                logger.warning("Speech provider unavailable for ingress: %s", exc)
                provider = None

        async def _one(item: IngestedAttachment) -> tuple[IngestedAttachment, Any]:
            try:
                result = await provider.transcribe(
                    item.audio_bytes, mime=item.mime or "audio/ogg"
                )
                return item, result
            except Exception as exc:
                logger.warning(
                    "Surface voice transcription failed path=%s error=%s",
                    item.path,
                    exc,
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

    async def _maybe_resume_pending_interaction(
        self, context: SurfaceChatContext, message_text: str
    ) -> bool:
        """Resume a paused ask_user or request_approval from a typed surface reply.

        Returns True when the message was consumed as an answer so the caller
        skips the normal new-message path. Best-effort: any failure returns False.

        ask_user: parses the reply as a numbered option (1, 2, …) or an exact
        label match, falling back to the raw text as a free-form "Other" answer.
        request_approval: "approve"/"yes"/… → APPROVE_ONCE; anything else → DENY.
        """
        if context.conversation_id is None:
            return False
        text = (message_text or "").strip()
        if not text:
            return False
        try:
            pending = await self.conversation_service.get_pending_user_interaction(
                conversation_id=context.conversation_id
            )
            if not isinstance(pending, dict):
                return False
            kind = str(pending.get("kind") or "")
            conversation = (
                await self.conversation_service.conversation_repository.get_conversation(
                    context.conversation_id
                )
            )
            if conversation is None:
                return False

            if kind == "ask_user":
                tool_args = pending.get("tool_args") or {}
                raw_request = tool_args.get("request") if isinstance(tool_args, dict) else None
                questions = []
                if isinstance(raw_request, dict):
                    try:
                        questions = AskUserRequest.model_validate(raw_request).questions
                    except Exception:
                        pass
                answers = _parse_ask_user_reply(text, questions)
                decision = AgentRunApprovalDecision.APPROVE_ONCE
                response: dict[str, Any] = {"answers": answers}
            else:
                decision = _parse_approval_decision(text)
                response = {}

            await self.conversation_service.resolve_user_approval_internal(
                conversation=conversation,
                approval_id=str(pending.get("tool_call_id") or ""),
                user_id=context.user_id,
                pod_id=context.pod_id,
                decision=decision,
                response=response,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Surface interaction typed-reply resume failed conversation=%s error=%s",
                context.conversation_id,
                exc,
            )
            return False

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
            return None

        surface = await self.surface_repository.get(link.surface_id)
        if surface is None or not surface.is_active:
            return None

        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None

        if not link.last_event:
            logger.warning(
                "Surface egress skipped: missing last_event conversation=%s",
                conversation_id,
            )
            return None
        try:
            parsed_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except Exception as exc:
            logger.warning(
                "Surface egress skipped: invalid last_event conversation=%s error=%s",
                conversation_id,
                exc,
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
        resolved.setdefault(
            "agent_display_name",
            await self._agent_name_for_agent_id(
                target.link.routed_agent_id or target.surface.agent_id
            )
            or "Lemma",
        )
        return resolved

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
        clean_message = strip_thinking_tokens(message)
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
            return False
        pending = await self.conversation_service.get_pending_ask_user(
            conversation_id=conversation_id
        )
        if not isinstance(pending, dict):
            return False
        tool_args = pending.get("tool_args")
        raw_request = tool_args.get("request") if isinstance(tool_args, dict) else None
        if not isinstance(raw_request, dict):
            return False
        try:
            request = AskUserRequest.model_validate(raw_request)
        except Exception as exc:
            logger.warning(
                "Surface ask_user render skipped (bad args) conversation=%s error=%s",
                conversation_id,
                exc,
            )
            return False
        if not request.questions:
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
        except Exception as exc:
            logger.warning(
                "Surface ask_user native render failed conversation=%s error=%s",
                conversation_id,
                exc,
            )
        # Fallback: a well-formatted text message; the user replies in chat and the
        # typed-reply path in start_agent_chat resumes the run with their answer.
        await target.adapter.send_message(
            credentials=target.credentials,
            event=target.event,
            message=render_questions_as_text(plan),
            metadata=metadata,
        )
        return True

    async def send_approval_prompt_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
    ) -> bool:
        """Render a pending ``request_approval`` as a text prompt on the surface.

        The user replies "approve" / "yes" to proceed or "deny" / "no" to cancel.
        The typed-reply path in ``start_agent_chat`` routes the answer back via
        ``_maybe_resume_pending_interaction``.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        pending = await self.conversation_service.get_pending_user_interaction(
            conversation_id=conversation_id
        )
        if not isinstance(pending, dict) or pending.get("kind") != "request_approval":
            return False
        tool_args = pending.get("tool_args") or {}
        title = str(tool_args.get("title") or "Action requires your approval")
        reason = str(tool_args.get("reason") or "").strip()
        inner_tool = str(tool_args.get("tool_name") or "")
        lines = [f"Approval needed: {title}"]
        if reason:
            lines.append(reason)
        if inner_tool:
            lines.append(f"Action: {inner_tool}")
        lines.append('\nReply "approve" to run it or "deny" to cancel.')
        metadata = await self._egress_metadata_with_agent_name(target, None)
        await target.adapter.send_message(
            credentials=target.credentials,
            event=target.event,
            message="\n".join(lines),
            metadata=metadata,
        )
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
        try:
            conversation = (
                await self.conversation_service.conversation_repository.get_conversation(
                    conversation_id
                )
            )
            if conversation is None:
                return False
            auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
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
        except Exception as exc:
            logger.warning(
                "Surface voice note fetch failed conversation=%s path=%s error=%s",
                conversation_id,
                path,
                exc,
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
        except Exception as exc:
            logger.warning(
                "Surface voice note send failed conversation=%s error=%s",
                conversation_id,
                exc,
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
            conversation = (
                await self.conversation_service.conversation_repository.get_conversation(
                    conversation_id
                )
            )
            if conversation is None:
                return False
            auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
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
        except Exception as exc:
            logger.warning(
                "Surface native file attach skipped conversation=%s path=%s error=%s",
                conversation_id,
                path,
                exc,
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

    async def try_handle_interaction(
        self,
        request: SurfacePlatformWebhookIngress | SurfaceDirectWebhookIngress,
    ) -> bool:
        """Parse + route an inbound interaction (native ask_user answer submit).

        Returns True when the payload was an interaction (handled or
        intentionally dropped); False when it is not an interaction and the
        caller should fall through to the normal message path.
        """
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
        try:
            parsed_callback = parse_callback_id(parsed.callback_id)
            if parsed_callback is None:
                return
            conversation_id_raw, tool_call_id = parsed_callback
            try:
                conversation_id = UUID(conversation_id_raw)
            except ValueError:
                return

            link = await self.conversation_link_repository.get_by_conversation_id(
                conversation_id
            )
            if link is None or link.platform != parsed.platform.value:
                return
            surface = await self.surface_repository.get(link.surface_id)
            if surface is None or not surface.is_active:
                return

            # Replay protection: each submission is processed once.
            claimed = await self.event_dedup_store.claim_message(
                surface_installation_id=surface.id,
                platform=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_message_id=parsed.dedup_id,
            )
            if not claimed:
                return

            # Authz: only the surface user who owns the conversation may submit
            # the answer that was shown to them.
            if (
                link.external_user_id
                and parsed.external_user_id
                and link.external_user_id != parsed.external_user_id
            ):
                logger.warning(
                    "Surface answer submission rejected: submitter=%s != owner=%s "
                    "conversation=%s",
                    parsed.external_user_id,
                    link.external_user_id,
                    conversation_id,
                )
                return

            conversation = (
                await self.conversation_service.conversation_repository.get_conversation(
                    conversation_id
                )
            )
            if conversation is None:
                return

            answers = merge_other_answers(parsed.values)
            auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
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
                    decision=AgentRunApprovalDecision.APPROVE_ONCE,
                    response={"answers": answers},
                )
            finally:
                reset_current_context(token)
        except Exception as exc:
            logger.warning("Surface interaction handling failed: %s", exc)

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
            return await target.adapter.stream_progress(
                credentials=target.credentials,
                event=target.event,
                progress_text=progress_text,
                progress_handle=progress_handle,
            )
        except Exception as exc:
            logger.warning(
                "Surface progress update failed conversation=%s error=%s",
                conversation_id,
                exc,
            )
            return progress_handle

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
        except Exception as exc:
            logger.warning(
                "Surface progress clear failed conversation=%s error=%s",
                conversation_id,
                exc,
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
                logger.info(
                    "Telegram mention matched bot @%s via entity chat=%s",
                    bot_username,
                    parsed.external_channel_id,
                )
                matched = True
            elif bot_user_id and bot_user_id in text_mention_user_ids:
                logger.info(
                    "Telegram text_mention matched bot id=%s chat=%s",
                    bot_user_id,
                    parsed.external_channel_id,
                )
                matched = True
            elif bot_username and f"@{bot_username}" in text:
                logger.info(
                    "Telegram text-mention fallback: @%s found in message text chat=%s",
                    bot_username,
                    parsed.external_channel_id,
                )
                matched = True

            if matched:
                return parsed.model_copy(update={"mentioned_agent": True})
        except Exception as exc:
            logger.debug("Telegram text-mention enrich failed: %s", exc)
        return parsed

    async def _resolve_credentials(
        self,
        surface: AgentSurfaceEntity,
    ) -> dict[str, Any]:
        return await self.credential_resolver.for_surface(surface)

    async def _resolve_credentials_from_context(
        self, context: AgentSurfaceContext
    ) -> dict[str, Any]:
        return await self.credential_resolver.for_platform(
            context.platform,
            context.surface_account_id,
        )

    def _resolve_platform(self, source: str) -> str | None:
        platform = SurfacePlatform.from_source(source)
        return platform.value if platform else None

    def _signup_message(self) -> str:
        signup_url = settings.auth_frontend_url.rstrip("/")
        return (
            "Please sign up before chatting with this agent. "
            f"You can get started here: {signup_url}"
        )

    def _reply_context(
        self,
        *,
        surface: AgentSurfaceEntity,
        parsed: ParsedInboundSurfaceEvent,
        agent_display_name: str | None,
        reply: tuple[str, dict[str, Any]],
    ) -> SurfaceReplyContext:
        message, reply_metadata = reply
        return SurfaceReplyContext(
            platform=surface.surface_type,
            surface_id=surface.id,
            surface_account_id=surface.account_id,
            surface_config=surface.config,
            agent_display_name=agent_display_name,
            reply_message=message,
            reply_metadata=reply_metadata,
            event=parsed,
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
            logger.info(
                "Agent surface ignored self-sent email surface=%s sender=%s",
                surface.id,
                parsed.sender_email,
            )
            return None

        if surface.should_ignore_sender(parsed.sender_external_user_id):
            logger.info(
                "Agent surface ignored event from bot sender=%s surface=%s",
                parsed.sender_external_user_id,
                surface.id,
            )
            return None

        claimed = await self.event_dedup_store.claim_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        )
        if not claimed:
            logger.info(
                "Agent surface ignored duplicate external message platform=%s channel=%s message_id=%s",
                surface.surface_type,
                parsed.external_channel_id,
                parsed.external_message_id,
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
                logger.info(
                    "Agent surface dropped event after enrichment platform=%s message_id=%s",
                    surface.surface_type,
                    parsed.external_message_id,
                )
                return None
            parsed = enriched
        except Exception as exc:
            logger.warning(
                "Failed enriching inbound event for %s message_id=%s: %s",
                surface.surface_type,
                parsed.external_message_id,
                exc,
            )

        # Re-check after enrichment: email triggers (e.g. Outlook) deliver a
        # minimal payload with no sender, so the pre-enrich self-check above
        # cannot see it. Without this the surface would process its own
        # outgoing replies and loop, re-sending the signup/agent reply forever.
        if self._is_self_email_event(surface=surface, parsed=parsed):
            logger.info(
                "Agent surface ignored self-sent email (post-enrich) surface=%s sender=%s",
                surface.id,
                parsed.sender_email,
            )
            return None

        attachment_count = len(parsed.metadata.get("attachments") or [])
        logger.info(
            "Agent surface prepared inbound event platform=%s message_id=%s attachments=%d",
            surface.surface_type,
            parsed.external_message_id,
            attachment_count,
        )

        if resolved_user is None:
            resolved_user = await self._resolve_sender_identity(
                adapter=adapter,
                parsed=parsed,
                credentials=credentials,
            )
        if resolved_user.internal_user_id is None:
            logger.info(
                "Agent surface could not resolve internal user for platform=%s sender=%s",
                surface.surface_type,
                parsed.sender_external_user_id,
            )
            # In a group/channel, never post a contact-share / signup prompt — it
            # would spam the group. Silently ignore senders we can't resolve; they
            # can DM the bot or link their profile (telegram_username / number).
            if not parsed.is_dm:
                return None
            reply = adapter.unresolved_sender_reply(parsed) or (
                self._signup_message(),
                {},
            )
            return self._reply_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
                reply=reply,
            )
        confirmation = adapter.linked_sender_confirmation(parsed)
        if confirmation is not None:
            return self._reply_context(
                surface=surface,
                parsed=parsed,
                agent_display_name=fallback_agent_display_name,
                reply=confirmation,
            )
        if (
            await self._match_surface_for_user(
                surfaces=[surface],
                resolved_user=resolved_user,
            )
            is None
        ):
            logger.info(
                "Agent surface resolved user is not a member of surface pod platform=%s surface=%s user=%s pod=%s",
                surface.surface_type,
                surface.id,
                resolved_user.internal_user_id,
                surface.pod_id,
            )
            return None
        if not surface.config.identity.allows_email(resolved_user.email):
            logger.info(
                "Agent surface identity policy rejected sender platform=%s surface=%s user=%s email=%s",
                surface.surface_type,
                surface.id,
                resolved_user.internal_user_id,
                resolved_user.email,
            )
            return None

        route = await self._resolve_route(surface=surface, parsed=parsed)
        if route is None:
            logger.info(
                "Agent surface ignored event with no matching route platform=%s surface=%s channel=%s",
                surface.surface_type,
                surface.id,
                parsed.external_channel_id,
            )
            return None

        link = await self._get_or_create_conversation_link(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            route=route,
        )

        return SurfaceChatContext(
            platform=surface.surface_type,
            pod_id=surface.pod_id,
            agent_name=route.agent_name,
            conversation_id=link.conversation_id,
            user_id=resolved_user.internal_user_id,
            surface_id=surface.id,
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
        if route.agent_name:
            agent = (
                await self.conversation_service.agent_repository.get_by_pod_and_name(
                    pod_id=surface.pod_id,
                    name=route.agent_name,
                )
            )
            if agent is not None:
                return agent.id, agent.name
            logger.warning(
                "Surface channel route agent '%s' not found in pod=%s; using surface default",
                route.agent_name,
                surface.pod_id,
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
            if not (
                parsed.mentioned_agent or parsed.metadata.get("is_thread_reply")
            ):
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
    ) -> AgentSurfaceConversationLink:
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
            if self._should_reset_dm_conversation(surface=surface, link=link):
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
                return updated or link
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
            return updated or link

        conversation = await self._create_surface_conversation(
            surface=surface,
            parsed=parsed,
            resolved_user=resolved_user,
            external_user_id=external_user_id,
            route=route,
        )
        return await self.conversation_link_repository.create(
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
            )
        )

    def _should_reset_dm_conversation(
        self,
        *,
        surface: AgentSurfaceEntity,
        link: AgentSurfaceConversationLink,
    ) -> bool:
        if surface.mode is not SurfaceMode.DM:
            return False
        reset_hours = surface.config.dm_conversation_reset_after_hours
        if reset_hours <= 0:
            return False
        last_seen = link.updated_at
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
        except Exception as exc:
            logger.warning(
                "Failed fetching sender profile for %s: %s",
                parsed.platform,
                exc,
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


def _ask_user_question_headers(tool_args: object) -> list[str]:
    """Extract question headers from a persisted ask_user tool call's args."""
    if not isinstance(tool_args, dict):
        return []
    request = tool_args.get("request")
    if not isinstance(request, dict):
        return []
    questions = request.get("questions")
    if not isinstance(questions, list):
        return []
    headers: list[str] = []
    for question in questions:
        if isinstance(question, dict):
            header = question.get("header")
            if isinstance(header, str) and header:
                headers.append(header)
    return headers


def _parse_ask_user_reply(
    text: str,
    questions: list,
) -> dict[str, Any]:
    """Map a typed surface reply to an ask_user answers dict.

    Single question: tries to match the text as a 1-based number or an exact
    case-insensitive option label; falls back to the raw text (free-form Other).
    Multiple questions: maps the raw text to every header — the agent receives
    the same string for all questions, which is the best we can do with a single
    unstructured reply.
    """
    if not questions:
        return {"answer": text}
    if len(questions) == 1:
        q = questions[0]
        options = getattr(q, "options", None) or []
        stripped = text.strip()
        # Number → option by 1-based index
        if stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(options):
                return {q.header: options[idx].label}
        # Case-insensitive label match
        lower = stripped.lower()
        for opt in options:
            if (getattr(opt, "label", "") or "").lower() == lower:
                return {q.header: opt.label}
        # Free-form Other
        return {q.header: stripped}
    # Multiple questions — crude but the only option for a plain text reply
    return {q.header: text for q in questions}


def _parse_approval_decision(text: str) -> "AgentRunApprovalDecision":
    """Parse a typed surface reply as an approval decision.

    "approve", "yes", "y", "ok", "confirm", "1", "run", "allow" → APPROVE_ONCE.
    Anything else → DENY (safe default).
    """
    from app.modules.agent.domain.value_objects import AgentRunApprovalDecision

    _APPROVE_WORDS = {"approve", "yes", "y", "ok", "okay", "confirm", "1", "run", "allow", "go"}
    if text.strip().lower() in _APPROVE_WORDS:
        return AgentRunApprovalDecision.APPROVE_ONCE
    return AgentRunApprovalDecision.DENY
