"""Starting the agent's turn on what arrived, in short scopes around long I/O.

The worker task `process_surface_message` is the only caller, and `execute_chat`
is the only method it calls. That is why this is its own object: the ingress
service took *either* a unit of work *or* a factory, and in factory mode seven
collaborators were `None` and about forty of its ninety-two methods would have
raised `AttributeError`. One signature produced two different objects, and it
survived because exactly one caller used the second one for exactly one method.

So the second one is this, and the constructor says what it is: a factory, an
adapter registry, file ingest, and the dedup store. There is nothing here that
holds a connection, because that is the whole point of the shape --
`start_agent_chat` runs platform API calls, file ingestion and voice
transcription, and only then opens a short scope to write. What it writes with
lives in `surface_inbound_message` as free functions over a unit of work, which
is what those became once nothing needed them to be methods on a namespace.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
    IngestedAttachment,
    SurfaceFileIngestService,
    every_attachment_failed,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    fetch_channel_context,
    transcribe_voice_attachments,
    write_inbound_message,
)
from app.modules.agent_surfaces.services.telegram_command_service import (
    handle_telegram_command,
)

logger = get_logger(__name__)


class SurfaceTurnStarter:
    """Everything between a queued inbound message and a run that is going."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
        file_ingest_service: SurfaceFileIngestService | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.file_ingest_service = file_ingest_service or SurfaceFileIngestService(
            adapter_registry=self.adapter_registry
        )
        self.event_dedup_store = event_dedup_store or get_surface_event_dedup_store()

    async def execute_chat(self, context: AgentSurfaceContext) -> None:
        """Answer, or -- for a sender we cannot place -- say so once and stop.

        Takes the parsed context. It used to also accept a raw dict and
        re-validate it through a `TypeAdapter`, which nothing ever needed:
        `SurfaceProcessMessageTaskPayload.context` is an `AgentSurfaceContext`,
        so pydantic has already validated it by the time the worker has a
        payload at all.
        """
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        if isinstance(context, SurfaceReplyContext):
            # Credentials are needed only to send the automated fallback.
            await deliver_fallback_reply(
                adapter=adapter,
                context=context,
                credentials=await self._credentials_for(context),
                event_dedup_store=self.event_dedup_store,
            )
            return

        await self.start_agent_chat(context)

    async def start_agent_chat(self, context: SurfaceChatContext) -> None:
        await self._validate_personal_context(context)
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        credentials = await self._credentials_for(context)
        if await handle_telegram_command(
            context=context,
            adapter=adapter,
            credentials=credentials,
            uow_factory=self.uow_factory,
        ):
            return
        with suppress(*PLATFORM_TRANSPORT_ERRORS):
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )

        # Auto-ingest any user-provided files into the pod datastore (/me/{platform})
        # so surface files behave like web uploads; failures never block the run.
        ingest = AttachmentIngest()
        if context.pod_id is not None:
            try:
                ingest = await self.file_ingest_service.ingest_attachments(
                    pod_id=context.pod_id,
                    platform=context.platform,
                    user_id=context.user_id,
                    parsed=context.event,
                    credentials=credentials,
                )
            except Exception as exc:
                # `ingest_attachments` reports a per-file failure itself, so
                # reaching here is the whole call coming apart. This was a
                # `suppress`, which left `ingest` empty -- indistinguishable
                # downstream from a message that carried no files, which is the
                # one thing `failed_files` below exists to prevent. The run
                # still goes ahead: losing the photo is not a reason to lose
                # the question that came with it.
                logger.warning(
                    "agent_surfaces.ingress_service.attachment_ingest_failed.degraded",
                    error_type=type(exc).__name__,
                    surface_id=str(context.surface_id) if context.surface_id else None,
                )
                ingest = every_attachment_failed(
                    context.event, reason="Lemma could not receive this file"
                )
        ingested: list[IngestedAttachment] = ingest.saved

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
        if ingest.failed:
            # The agent has to be able to tell "they sent nothing" from "they
            # sent something I never received" — otherwise it answers the text
            # alone and looks like it ignored the photo.
            metadata["failed_files"] = [
                {"name": item.name, "reason": item.reason} for item in ingest.failed
            ]

        # Group/channel continuity: each user has a separate conversation, so fetch
        # the last few thread/channel messages fresh for THIS run and hand them to
        # the agent as background context. Best-effort; never blocks the run.
        if not context.event.is_dm:
            channel_context = await fetch_channel_context(
                adapter=adapter, context=context, credentials=credentials
            )
            if channel_context:
                metadata["channel_context"] = channel_context

        # Transcribe inbound voice notes here so the agent just reads the user's
        # words; the audio file stays saved for replay / re-listening.
        message_text = await transcribe_voice_attachments(
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
            except PLATFORM_TRANSPORT_ERRORS:
                # A platform call, so a platform failure. It caught
                # `SQLAlchemyError` -- the wrong family entirely, and nothing
                # here touches the database: Slack's implementation catches only
                # `SlackApiError`, so a timeout or a dropped connection while
                # naming the thread escaped, `start_agent_chat` exited before
                # `write_inbound_message`, and the person's *first* message on a
                # brand-new conversation was silently dropped along with the run
                # it should have started.
                logger.debug(
                    "agent_surfaces.ingress_service.surface_thread_title_set.diagnostic",
                    conversation_id=context.conversation_id,
                )

        # No acknowledgement for a message that lands mid-run. One message is
        # routinely several webhooks on a chat surface, so "I'll get to this"
        # was mostly the agent talking to itself about its own plumbing -- and
        # it is no longer true: `PendingUserMessagesCapability` steers these
        # into the run already going, which answers all of them at once.
        async with self.uow_factory() as uow:
            await write_inbound_message(context, message_text, metadata, uow)

    async def _validate_personal_context(self, context: SurfaceChatContext) -> None:
        if context.personal_dm_route_id is not None:
            from app.modules.agent_surfaces.services.personal_dm_routes import (
                PersonalRouteUnavailable,
                validate_personal_dm_route,
            )

            async with self.uow_factory() as uow:
                route = await validate_personal_dm_route(
                    uow, route_id=context.personal_dm_route_id, event=context.event
                )
                if (route.user_id, route.pod_id, route.installation_surface_id) != (
                    context.user_id,
                    context.pod_id,
                    context.surface_id,
                ):
                    raise PersonalRouteUnavailable(
                        "The queued personal destination no longer matches"
                    )

    async def _credentials_for(self, context: AgentSurfaceContext) -> dict[str, Any]:
        """Credentials for a reply that has only the run's context in hand.

        Read in a short scope of its own and returned as a plain dict, so no
        connection is held during the platform I/O that follows.

        Resolved *for the surface* when the context names one and the platform
        needs it: ``for_platform`` cannot know a value that lives on the surface
        row, and every agent reply on an email surface failed for want of it.
        The extra read is Resend's alone -- its ``from_address`` is the only
        credential value stored per surface -- so it stays off every other
        platform's inbound path.
        """
        surface_id = context.surface_id
        is_resend = str(context.platform or "").upper() == SurfacePlatform.RESEND.value
        async with self.uow_factory() as uow:
            resolver = SurfaceCredentialResolver(uow=uow)
            if surface_id is not None and is_resend:
                surface = await SurfaceRepository(uow).get(surface_id)
                if surface is not None:
                    return await resolver.for_surface(surface)
            return await resolver.for_platform(
                context.platform, context.surface_account_id, surface=None
            )
