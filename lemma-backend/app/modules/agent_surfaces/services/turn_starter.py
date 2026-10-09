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
from dataclasses import dataclass
from typing import Any

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.ingress_context import (
    AgentSurfaceContext,
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.groups import SurfaceGroup
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.adapters.routing_resolution_adapter import (  # noqa: E501
    SqlAlchemySurfaceRoutingResolutionAdapter,
)
from app.modules.agent_surfaces.infrastructure.adapters.user_directory_adapter import (
    IdentityUserDirectoryAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.resend.email_recipients import (
    reply_all_recipients,
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
from app.modules.agent_surfaces.services.group_log import (
    GroupBackground,
    for_member_run,
    group_background,
    keeps_group_log,
)
from app.modules.agent_surfaces.services.group_audience import (
    Audience,
    chat_audience,
    email_audience,
    outside_names,
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


@dataclass(frozen=True, slots=True)
class _RunBackground:
    """The group as one run is shown it."""

    lines: list[dict[str, object]]
    withheld: int = 0
    audience: Audience | None = None


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
    ) -> None:
        self.uow_factory = uow_factory
        # `None` reads the pool through each short scope's own unit of work.
        self.pooled_numbers = pooled_numbers
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
        if await self._waits_for_a_person(context):
            await self._hand_to_a_person(context, adapter, credentials)
            return
        with suppress(*PLATFORM_TRANSPORT_ERRORS):
            await adapter.add_processing_indicator(
                credentials=credentials,
                event=context.event,
                metadata={
                    "agent_display_name": context.agent_display_name,
                },
            )

        ingest = await self._ingest_files(context, credentials)
        ingested: list[IngestedAttachment] = ingest.saved
        metadata = _message_metadata(context, ingest)

        # Group/channel continuity: each user has a separate conversation, so fetch
        # the last few thread/channel messages fresh for THIS run and hand them to
        # the agent as background context. Best-effort; never blocks the run.
        if not context.event.is_dm:
            background = await self._background(
                context, adapter=adapter, credentials=credentials
            )
            if background.lines:
                metadata["channel_context"] = background.lines
            if background.withheld:
                metadata["channel_context_withheld"] = background.withheld
            if background.audience is not None:
                metadata["outside_audience"] = background.audience.to_metadata()
        elif not context.audience.answers_outsiders:
            # Email is a DM to the pod's mailbox, but a reply-all reaches
            # everybody on the thread.
            audience = await self._email_audience(context)
            if audience is not None:
                metadata["outside_audience"] = audience.to_metadata()

        # Transcribe inbound voice notes here so the agent just reads the user's
        # words; the audio file stays saved for replay / re-listening.
        message_text = await transcribe_voice_attachments(
            ingested=ingested,
            original_text=context.message_text,
            metadata=metadata,
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
        async with self.uow_factory() as uow:
            await write_inbound_message(context, message_text, metadata, uow)

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
        credentials: dict[str, object],
    ) -> None:
        """Keep the message for the member, and tell the person once a day."""
        if context.pod_id is None:
            return
        tell = await first_word_today(context.conversation_id)
        async with self.uow_factory() as uow:
            held = await hand_to_a_person(
                uow,
                tell=tell,
                pod_id=context.pod_id,
                conversation_id=context.conversation_id,
                owner_id=context.user_id,
                # Not transcribed or saved: no run will read it, and an
                # outsider's files are never saved (see `_ingest_files`).
                text=context.message_text.strip() or "[a message with no text]",
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
        except (AgentSurfaceError, *PLATFORM_TRANSPORT_ERRORS):
            # The message is kept and the member told; only the courtesy line
            # did not land, and the conversation already shows it.
            logger.warning(
                "agent_surfaces.outside_cap.reply_not_sent.degraded",
                platform=context.platform.value,
                exc_info=True,
            )

    async def _ingest_files(
        self, context: SurfaceChatContext, credentials: dict[str, Any]
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

    async def _background(
        self,
        context: SurfaceChatContext,
        *,
        adapter: SurfacePlatformAdapterPort,
        credentials: dict[str, object],
    ) -> _RunBackground:
        """What this run is shown of the group, and who outside the pod reads it.

        The pod's own log where it keeps one (Telegram, WhatsApp); otherwise the
        platform's history, read live. Either way a member's run is not shown
        what strangers wrote (``group_log``), and is told that they are there.
        """
        group, logged = await self._group_background(context)
        if logged is not None:
            lines = [line.model_dump(mode="json") for line in logged.lines]
            withheld, outside = logged.withheld, list(logged.outside_authors)
        else:
            lines = await fetch_channel_context(
                adapter=adapter, context=context, credentials=credentials
            )
            outside = outside_names(lines)
            withheld = 0
            if not context.audience.answers_outsiders:
                lines, withheld = for_member_run(lines)
        # The message being answered was logged on its way in and is already
        # the prompt; repeating it as background shows the agent the question
        # twice. It is the latest line in all but a race, so only that one goes.
        if lines and lines[-1].get("text") == context.event.message_text.strip():
            lines.pop()
        audience = (
            None
            if context.audience.answers_outsiders
            else chat_audience(
                platform=context.platform,
                group=group,
                parsed=context.event,
                outside_authors=outside,
            )
        )
        return _RunBackground(lines=lines, withheld=withheld, audience=audience)

    async def _group_background(
        self, context: SurfaceChatContext
    ) -> tuple[SurfaceGroup | None, GroupBackground | None]:
        """The group the pod keeps a log for, and that log; else nothing.

        Nothing where the platform can read its history (Slack, Teams) or the
        group is not one the pod keeps a log for -- the caller then asks the
        platform, as it always has, and reads a shared Slack channel off the
        event itself.
        """
        channel_id = context.event.external_channel_id
        if (
            context.surface_id is None
            or not channel_id
            or not keeps_group_log(context.platform.value)
        ):
            return None, None
        async with self.uow_factory() as uow:
            group = await SurfaceGroupRepository(uow.session).get(
                surface_id=context.surface_id, external_channel_id=channel_id
            )
            if group is None:
                return None, None
            logged = await group_background(
                uow,
                group=group,
                membership=SqlAlchemySurfaceRoutingResolutionAdapter(uow),
                agent_display_name=context.agent_display_name,
                for_stranger=context.audience.answers_outsiders,
            )
        return group, logged

    async def _email_audience(self, context: SurfaceChatContext) -> Audience | None:
        """Who outside the pod a reply on this email thread also reaches."""
        if context.platform is not SurfacePlatform.RESEND or context.pod_id is None:
            return None
        recipients = reply_all_recipients(context.event)
        if not recipients:
            return None
        pod_id = context.pod_id
        async with self.uow_factory() as uow:
            people = IdentityUserDirectoryAdapter(uow)
            membership = SqlAlchemySurfaceRoutingResolutionAdapter(uow)

            async def is_member(address: str) -> bool:
                user_id = await people.user_id_by_email(address)
                return user_id is not None and pod_id in set(
                    await membership.get_user_pod_ids(user_id)
                )

            return await email_audience(recipients=recipients, is_member=is_member)

    async def _name_new_thread(
        self,
        adapter: SurfacePlatformAdapterPort,
        credentials: dict[str, Any],
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
        except PLATFORM_TRANSPORT_ERRORS:
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
