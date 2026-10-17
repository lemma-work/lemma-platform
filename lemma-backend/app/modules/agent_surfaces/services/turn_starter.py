"""Starting the agent's turn on what arrived, in short scopes around long I/O.

The work item `surface.ingest` is the only production caller, and `execute_chat`
is the only method it calls. That is why this is its own object: the ingress
service took *either* a unit of work *or* a factory, and in factory mode seven
collaborators were `None` and about forty of its ninety-two methods would have
raised `AttributeError`. One signature produced two different objects, and it
survived because exactly one caller used the second one for exactly one method.

So the second one is this, and the constructor says what it is: a factory, an
adapter registry, file ingest, and the dedup store. There is nothing here that
holds a connection, because that is the whole point of the shape --
`start_agent_chat` runs platform API calls, file ingestion and voice
transcription, and only then opens a short scope to write -- the ingest item's
fenced completion, so the message and the item's end are one commit. What it
writes with
lives in `surface_inbound_message` as free functions over a unit of work, which
is what those became once nothing needed them to be methods on a namespace.
"""

from __future__ import annotations

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.personal_route import PersonalRouteUnavailable
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.channel_lines import (
    ChannelLinesFor,
    ChannelLinesInDatabase,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    PooledNumberReader,
    SurfaceCredentialResolver,
    arrival_number,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    deliver_fallback_reply,
    to_sender_alone,
)
from app.modules.agent_surfaces.services.outside_cap import (
    first_word_today,
    hand_to_a_person,
    held_for_a_person,
)
from app.modules.agent_surfaces.services.plain_reply import reply_text
from app.modules.agent_surfaces.services.attachment_ingest_memo import (
    AttachmentIngestMemo,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    AttachmentIngest,
    IngestedAttachment,
    SurfaceFileIngestService,
    every_attachment_failed,
)
from app.modules.agent_surfaces.domain.decision_card import CardRetirement
from app.modules.agent_surfaces.services.held_replies import stage_held_replies
from app.modules.agent_surfaces.services.message_claims import SurfaceEventDedupStore
from app.modules.agent_surfaces.services.card_retirement import (
    retire_superseded_cards,
)
from app.modules.agent_surfaces.services.group_log import RosterReads, roster_reads
from app.modules.agent_surfaces.services.run_background import (
    reply_all_audience,
    run_background,
)
from app.modules.agent_surfaces.services.surface_inbound_message import (
    transcribe_voice_attachments,
    write_inbound_message,
)
from app.modules.agent_surfaces.services.telegram_command_service import (
    handle_telegram_command,
    handle_typed_retry,
)

logger = get_logger(__name__)

#: Prepare an inbound delivery the ordinary way; see ``reprepare``.
type Reprepare = Callable[
    [SurfacePlatformWebhookIngress], Awaitable[AgentSurfaceContext | None]
]
#: The transaction the inbound message is written in. ``surface.ingest`` hands
#: in its fenced completion, so the message, the run, its ``agent.turn`` item
#: and the inbound row going ROUTED commit together or not at all.
type WriteScope = Callable[[], AbstractAsyncContextManager[SqlAlchemyUnitOfWork]]
#: Takes a reply that is not a run (a stranger's sign-up link, a refusal) to
#: deliver it durably instead of sending it here. True: taken.
type ReplySink = Callable[[SurfaceReplyContext], Awaitable[bool]]

_COULD_NOT_READ = "I couldn't read that message. Could you send it again?"
PROCESSING_FAILED_REPLY = "I couldn't process that message. Please send it again."


class SurfaceTurnStarter:
    """Everything between a queued inbound message and a run that is going."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        adapter_registry: SurfacePlatformAdapterRegistry | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
        file_ingest_service: SurfaceFileIngestService | None = None,
        pooled_numbers: PooledNumberReader | None = None,
        ingest_memo: AttachmentIngestMemo | None = None,
        reprepare: Reprepare | None = None,
        roster_reads: RosterReads = roster_reads,
        write_scope: WriteScope | None = None,
        reply_sink: ReplySink | None = None,
        channel_lines: ChannelLinesFor = ChannelLinesInDatabase,
    ) -> None:
        self.uow_factory = uow_factory
        self.channel_lines = channel_lines
        self.write_scope: WriteScope = write_scope or uow_factory
        self.reply_sink = reply_sink
        self.roster_reads = roster_reads
        # Shared with the file ingest by Redis key, not by instance: what a
        # retry reuses is keyed on the message, whichever object reads it.
        self.ingest_memo = ingest_memo or AttachmentIngestMemo()
        # Ordinary ingress, for a message whose queued private route went away.
        # Injected because preparing a message needs the request-mode ingress
        # service, which this worker-side object deliberately is not.
        self.reprepare = reprepare
        # `None` reads the pool through each short scope's own unit of work.
        self.pooled_numbers = pooled_numbers
        self.adapter_registry = adapter_registry or SurfacePlatformAdapterRegistry()
        self.file_ingest_service = file_ingest_service or SurfaceFileIngestService(
            adapter_registry=self.adapter_registry
        )
        self.event_dedup_store = event_dedup_store or SurfaceEventDedupStore(
            uow_factory=uow_factory
        )

    async def execute_chat(self, context: AgentSurfaceContext) -> None:
        """Answer, or -- for a sender we cannot place -- say so once and stop.

        Takes the parsed context, prepared by ``surface.ingest`` from the
        stored inbound row.
        """
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        if isinstance(context, SurfaceReplyContext):
            if self.reply_sink is not None and await self.reply_sink(context):
                return
            # Credentials are needed only to send the automated fallback.
            await deliver_fallback_reply(
                adapter=adapter,
                context=context,
                credentials=await self._credentials_for(context),
                event_dedup_store=self.event_dedup_store,
            )
            return

        try:
            await self.start_agent_chat(context)
        except PersonalRouteUnavailable as unavailable:
            # The private destination this was queued for went away between
            # preparing the message and starting its turn (the person left the
            # pod, the app was uninstalled). Retrying cannot bring it back; the
            # message is still the person's, so it goes the ordinary way.
            await self._reroute_through_ordinary_ingress(context, unavailable)

    async def start_agent_chat(self, context: SurfaceChatContext) -> None:
        await self._validate_personal_context(context)
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None:
            return

        credentials = await self._credentials_for(context)
        for handled_here in (handle_telegram_command, handle_typed_retry):
            if await handled_here(
                context=context,
                adapter=adapter,
                credentials=credentials,
                uow_factory=self.uow_factory,
            ):
                return
        if await self._waits_for_a_person(context):
            await self._hand_to_a_person(context, adapter, credentials)
            return
        # Matched after the raise rather than taken up front as `suppress` would:
        # this call may be the one that first loads Slack's SDK, whose errors
        # join the family only once it is loaded.
        try:
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )
        except platform_transport_errors():
            pass

        ingest = await self._ingest_files(context, credentials)
        ingested: list[IngestedAttachment] = ingest.saved
        metadata = _message_metadata(context, ingest)

        # Group/channel continuity: each user has a separate conversation, so fetch
        # the last few thread/channel messages fresh for THIS run and hand them to
        # the agent as background context. Best-effort; never blocks the run.
        if not context.event.is_dm:
            background = await run_background(
                self.uow_factory,
                context,
                adapter=adapter,
                credentials=credentials,
                roster_reads=self.roster_reads,
            )
            metadata.update(background.to_metadata())
        elif not context.audience.answers_outsiders:
            # Email is a DM to the pod's mailbox, but a reply-all reaches
            # everybody on the thread.
            audience = await reply_all_audience(self.uow_factory, context)
            if audience is not None:
                metadata["outside_audience"] = audience.to_metadata()

        # Transcribe inbound voice notes here so the agent just reads the user's
        # words; the audio file stays saved for replay / re-listening.
        message_text = await transcribe_voice_attachments(
            ingested=ingested,
            original_text=context.message_text,
            metadata=metadata,
            memo=self.ingest_memo,
        )
        await self._name_new_thread(adapter, credentials, context)

        # The only DB writes happen here, AFTER all the external I/O above — so
        # in worker (factory) mode a pooled connection is held just for this
        # short tail, not across the platform/file/transcription calls.
        #
        # No acknowledgement for a message that lands mid-run. One message is
        # routinely several webhooks on a chat surface, so "I'll get to this"
        # was mostly the agent talking to itself about its own plumbing -- and
        # it is no longer true: `PendingUserMessagesCapability` steers these
        # into the run already going, which answers all of them at once.
        async with self.write_scope() as uow:
            finished = await write_inbound_message(
                context, message_text, metadata, uow, lines=self.channel_lines(uow)
            )
            # Their message reopened a closed reply window: what waited for it
            # is written out now, ahead of the answer this message will get,
            # and the destination pump sends them in that order.
            await stage_held_replies(
                uow,
                conversation_id=context.conversation_id,
                pod_id=context.pod_id,
                platform=context.platform,
            )
        for tool_call_ids, reason in (
            (finished.superseded, CardRetirement.SUPERSEDED),
            (finished.answered, CardRetirement.ANSWERED),
        ):
            await retire_superseded_cards(
                adapter=adapter,
                credentials=credentials,
                conversation_id=context.conversation_id,
                tool_call_ids=tool_call_ids,
                reason=reason,
            )
        if not message_text.strip() and not context.event.metadata.get("attachments"):
            # Nothing a person sent is empty: a body we failed to fetch or
            # parse. The write declined it, and silence reads as being ignored.
            await self.tell_sender(context, _COULD_NOT_READ)

    async def tell_sender(self, context: AgentSurfaceContext, message: str) -> None:
        """A short reply to the person, outside any run, at most once per window.

        For a message that will not be answered: one that could not be read, or
        one whose processing failed for good. Through the fallback path, which
        holds the stranger-reply window, so a burst of failures is one reply.
        Never raises: it runs where something has already gone wrong, and its
        own failure must not replace that one.
        """
        adapter = self.adapter_registry.get(context.platform)
        if adapter is None or not context.event.sender_external_user_id:
            return
        reply = SurfaceReplyContext(
            platform=context.platform,
            surface_id=context.surface_id,
            surface_name=context.surface_name,
            surface_account_id=context.surface_account_id,
            surface_config=context.surface_config,
            agent_display_name=context.agent_display_name,
            event=context.event,
            reply_kind="processing_failed",
            reply_message=message,
        )
        try:
            await deliver_fallback_reply(
                adapter=adapter,
                context=reply,
                credentials=await self._credentials_for(reply),
                event_dedup_store=self.event_dedup_store,
            )
        except (SQLAlchemyError, *platform_transport_errors()):
            logger.warning(
                "agent_surfaces.turn_starter.failure_reply_not_sent.degraded",
                platform=str(context.platform),
                exc_info=True,
            )

    async def _reroute_through_ordinary_ingress(
        self, context: SurfaceChatContext, unavailable: Exception
    ) -> None:
        logger.info(
            "agent_surfaces.turn_starter.personal_route_gone.rerouted",
            platform=str(context.platform),
            reason=str(unavailable),
        )
        if self.reprepare is None or not context.event.raw_payload:
            await self.tell_sender(context, PROCESSING_FAILED_REPLY)
            return
        # Preparing it again takes the message claim again; it is this
        # inbound row's own, so it is granted.
        rerouted = await self.reprepare(
            SurfacePlatformWebhookIngress(
                source=context.platform.value.lower(),
                payload=context.event.raw_payload,
            )
        )
        if isinstance(rerouted, SurfaceChatContext) and (
            rerouted.personal_dm_route_id is not None
        ):
            # Ordinary ingress does not mint personal routes; one coming back
            # would loop.
            await self.tell_sender(context, PROCESSING_FAILED_REPLY)
            return
        if rerouted is not None:
            await self.execute_chat(rerouted)

    async def _waits_for_a_person(self, context: SurfaceChatContext) -> bool:
        """Whether an outsider's turn starts no run, before anything is spent on it.

        Asked ahead of the typing indicator, file handling and transcription:
        a turn no run will answer should not look as if one is.
        """
        if not context.audience.answers_outsiders or context.pod_id is None:
            return False
        async with self.uow_factory() as uow:
            return await held_for_a_person(
                uow, pod_id=context.pod_id, conversation_id=context.conversation_id
            )

    async def _hand_to_a_person(
        self,
        context: SurfaceChatContext,
        adapter: SurfacePlatformAdapterPort,
        credentials: SurfaceCredentials,
    ) -> None:
        """Keep the message for the member, and tell the person once a day."""
        if context.pod_id is None:
            return
        tell = await first_word_today(context.conversation_id)
        text = context.message_text.strip() or "[a message with no text]"
        async with self.uow_factory() as uow:
            # On the channel's timeline whether or not a run answers it: the
            # member answering by hand reads it there.
            await self.channel_lines(uow).heard(context, text=text)
            held = await hand_to_a_person(
                uow,
                tell=tell,
                pod_id=context.pod_id,
                conversation_id=context.conversation_id,
                owner_id=context.user_id,
                # Not transcribed or saved: no run will read it, and an
                # outsider's files are never saved (see `_ingest_files`).
                text=text,
                metadata=_message_metadata(context, AttachmentIngest()),
            )
        if held.reply is None:
            return
        try:
            await reply_text(
                adapter=adapter,
                credentials=credentials,
                event=to_sender_alone(context.event),
                message=held.reply,
                metadata={"agent_display_name": context.agent_display_name},
            )
        except (AgentSurfaceError, *platform_transport_errors()):
            # The message is kept and the member told; only the courtesy line
            # did not land, and the conversation already shows it.
            logger.warning(
                "agent_surfaces.outside_cap.reply_not_sent.degraded",
                platform=context.platform.value,
                exc_info=True,
            )

    async def _ingest_files(
        self, context: SurfaceChatContext, credentials: SurfaceCredentials
    ) -> AttachmentIngest:
        """Auto-ingest user-provided files into the pod datastore (/me/{platform}).

        Surface files behave like web uploads; failures never block the run.

        Not a stranger's: saving them would put their files in the owner's own
        space, where the stranger's run -- reading only what is Public -- could
        not open them anyway. The run is told, so it does not look like it
        ignored the file.
        """
        if context.audience.answers_outsiders:
            return every_attachment_failed(
                context.event, reason="Files from outside the pod are not saved"
            )
        if context.pod_id is None:
            return AttachmentIngest()
        try:
            return await self.file_ingest_service.ingest_attachments(
                pod_id=context.pod_id,
                platform=context.platform,
                user_id=context.user_id,
                parsed=context.event,
                credentials=credentials,
            )
        except Exception as exc:
            raise_if_database_unavailable(exc)
            # `ingest_attachments` reports a per-file failure itself, so
            # reaching here is the whole call coming apart. This was a
            # `suppress`, which left `ingest` empty -- indistinguishable
            # downstream from a message that carried no files, which is the
            # one thing `failed_files` exists to prevent. The run still goes
            # ahead: losing the photo is not a reason to lose the question that
            # came with it.
            logger.warning(
                "agent_surfaces.ingress_service.attachment_ingest_failed.degraded",
                error_type=type(exc).__name__,
                surface_id=str(context.surface_id) if context.surface_id else None,
            )
            return every_attachment_failed(
                context.event, reason="Lemma could not receive this file"
            )

    async def _name_new_thread(
        self,
        adapter: SurfacePlatformAdapterPort,
        credentials: SurfaceCredentials,
        context: SurfaceChatContext,
    ) -> None:
        """Name the thread on the platform when this turn created the conversation.

        A brand-new conversation is the one moment worth naming the thread, so
        Slack's own DM history reads as a list of topics rather than a stack of
        identical bot threads. Best-effort by construction: the adapter returns
        False where it is unsupported.
        """
        if not context.created_conversation_title:
            return
        try:
            await adapter.set_thread_title(
                credentials=credentials,
                event=context.event,
                title=context.created_conversation_title,
            )
        except platform_transport_errors():
            # A platform call, so a platform failure. It caught
            # `SQLAlchemyError` -- the wrong family entirely, and nothing here
            # touches the database: Slack's implementation catches only
            # `SlackApiError`, so a timeout or a dropped connection while naming
            # the thread escaped, `start_agent_chat` exited before
            # `write_inbound_message`, and the person's *first* message on a
            # brand-new conversation was silently dropped along with the run it
            # should have started.
            logger.debug(
                "agent_surfaces.ingress_service.surface_thread_title_set.diagnostic",
                conversation_id=context.conversation_id,
            )

    async def _validate_personal_context(self, context: SurfaceChatContext) -> None:
        if context.personal_dm_route_id is not None:
            from app.modules.agent_surfaces.services.personal_dm_routes import (
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

    async def _credentials_for(
        self, context: AgentSurfaceContext
    ) -> SurfaceCredentials:
        """Credentials for a reply that has only the run's context in hand.

        Read in a short scope of its own and returned as a plain dict, so no
        connection is held during the platform I/O that follows.

        Resolved *for the surface* when the context names one and the platform
        needs it: ``for_platform`` cannot know a value that lives on the surface
        row, and every agent reply on an email surface failed for want of it.
        The extra read is Resend's alone -- its ``from_address`` is the only
        credential value stored per surface -- so it stays off every other
        platform's inbound path.

        The number the message arrived on is passed through for the same
        reason in reverse: the read receipt, the typing indicator, the media
        download and the fallback reply all act on *that* message, so on a
        pooled WhatsApp number they need that number's token, not the one in
        settings.
        """
        surface_id = context.surface_id
        is_resend = str(context.platform or "").upper() == SurfacePlatform.RESEND.value
        async with self.uow_factory() as uow:
            resolver = SurfaceCredentialResolver(
                uow=uow, pooled_numbers=self.pooled_numbers
            )
            if surface_id is not None and is_resend:
                surface = await SurfaceRepository(uow).get(surface_id)
                if surface is not None:
                    return await resolver.for_surface(surface)
            return await resolver.for_platform(
                context.platform,
                context.surface_account_id,
                surface=None,
                arrived_on=arrival_number(context.event),
            )


def _message_metadata(
    context: SurfaceChatContext, ingest: AttachmentIngest
) -> dict[str, Any]:
    """What the run learns about this message beyond its text."""
    metadata = context.message_metadata.as_message_metadata()
    metadata.update(
        {
            "source": "agent_surfaces",
            "surface_id": str(context.surface_id) if context.surface_id else None,
            "external_user_id": context.message_external_user_id,
            "external_message_id": context.message_external_message_id,
        }
    )
    if ingest.saved:
        metadata["ingested_files"] = [item.path for item in ingest.saved]
    if ingest.failed:
        # The agent has to be able to tell "they sent nothing" from "they
        # sent something I never received" — otherwise it answers the text
        # alone and looks like it ignored the photo.
        metadata["failed_files"] = [
            {"name": item.name, "reason": item.reason} for item in ingest.failed
        ]
    return metadata
