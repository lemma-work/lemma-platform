"""What Lemma says on a chat surface, and how each thing is shaped to be said.

Eight verbs, one per thing an agent run can put in front of a person: an answer,
a file or table, a set of questions, an approval card, a sign-in link, a voice
note, and -- for a recipient who has never written to us -- a cold email. Each
builds one :class:`SurfaceEnvelope` and hands it to :class:`SurfaceDelivery`,
which is the only thing here that talks to a platform.

This was ``SurfaceEgressMixin``, one of eight bases on a service that also
handled inbound events, routing, configuration and interactions; the type
checker could not see where ``self.uow`` or ``self.adapter_registry`` came from,
and those unresolvable names were 85% of the repository's baselined type errors.
It is an object with a constructor now. The live-progress verbs went to
:class:`SurfaceProgress` and ``send_to_member`` to :class:`MemberReach`, each
for a reason written down there.

Nothing here reads an inbound event.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.core.file_types import is_untyped_mime
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.conversations_for_surfaces import PendingInteraction
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.domain.envelope import EnvelopeVoice, SurfaceEnvelope
from app.modules.agent_surfaces.domain.ports import ColdEmailThread
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
)
from app.modules.agent_surfaces.services.display_resource_content import (
    apply_file_facts,
    apply_table_rows,
    load_pod_file_bytes,
    resolve_pod_file_parts,
    resolve_table_preview,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_ask_user_render_plan,
    build_display_resource_render_plan,
)
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery
from app.modules.agent_surfaces.services.egress_progress import SurfaceProgress
from app.modules.agent_surfaces.services.approval_cards import approval_plan
from app.modules.agent_surfaces.services.free_text_answer import (
    remember_free_text_answer_wanted,
    tool_call_id_of,
)
from app.modules.agent_surfaces.services.one_reply_attachments import (
    held_files_for_run,
    release_held_files,
)
from app.modules.agent_surfaces.services.pending_envelope import RunFiles
from app.modules.agent.contracts.interaction_replies import ask_user_request_dict
from app.modules.agent_surfaces.services.surface_sign_in import (
    sign_in_prompt_envelope,
)

logger = get_logger(__name__)


class SurfaceEgress:
    """Everything an agent run can say on a surface, as envelopes."""

    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        delivery: SurfaceDelivery,
        progress: SurfaceProgress,
    ) -> None:
        self.uow = uow
        self.delivery = delivery
        # Held rather than inherited. The run observer drives both halves and
        # used to reach thirteen flattened verbs on one namespace; `egress` and
        # `egress.progress` say which API each call is speaking.
        self.progress = progress

    async def agent_name_for_surface(self, surface: AgentSurfaceEntity) -> str | None:
        """Part of :class:`SurfaceNotificationEgressPort`; see `agent_naming`."""
        return await self.delivery.agent_name_for_surface(surface)

    async def open_cold_email_thread(
        self,
        *,
        surface: AgentSurfaceEntity,
        recipient_email: str,
        subject: str,
        message: str,
        thread_seed_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> ColdEmailThread | None:
        """Email somebody who has never written to us, and remember the thread.

        The one verb here that does not resolve a target: a target is a *stored
        link*, and the whole point of a cold open is that there is not one yet.
        Returns None when the surface is inactive, has no adapter, or sits on a
        platform that cannot start a thread -- all of which are "no", not
        failures.
        """
        if not surface.is_active:
            return None
        adapter = self.delivery.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None
        clean_message = sanitize_user_visible_text(message)
        if not clean_message:
            return None
        sent = await adapter.send_cold_email(
            credentials=await self.delivery.egress_credentials(surface),
            recipient_email=recipient_email,
            subject=subject,
            message=clean_message,
            thread_seed_id=thread_seed_id,
            metadata=metadata,
        )
        if sent is None:
            return None
        return build_cold_email_thread(
            surface=surface, recipient_email=recipient_email, sent=sent
        )

    async def send_agent_message_for_conversation(
        self,
        *,
        conversation_id: UUID,
        message: str,
        metadata: dict[str, Any] | None = None,
        attach_files_of: RunFiles | None = None,
    ) -> bool:
        """Send a message; ``attach_files_of`` names the run whose held files ride it.

        Left out, nothing is attached: a notification or a nudge is not the reply
        those files were shown for, and one run's files are never another's.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        # Safety net: never deliver model reasoning/thinking tokens
        # (``<tool_call>…``) as a chat message to any surface. Some
        # OpenAI-compatible models emit these inline in the text content.
        clean_message = sanitize_user_visible_text(message)
        if not clean_message:
            return False
        # A failure notice is not the reply the files were shown for. Attaching
        # them to it sent a person a half-finished run's files under an apology,
        # and -- since held files were drained on read -- spent them on it.
        files, held = (
            ([], [])
            if (metadata or {}).get("retry_action") or attach_files_of is None
            else await held_files_for_run(
                uow=self.uow,
                target=target,
                conversation_id=conversation_id,
                run=attach_files_of,
            )
        )
        delivered = await self.delivery.deliver_envelope(
            target,
            envelope=SurfaceEnvelope(text=clean_message, files=files),
            metadata=await self.delivery.egress_metadata(target, metadata),
            conversation_id=conversation_id,
        )
        if delivered and held and attach_files_of is not None:
            await release_held_files(
                uow=self.uow,
                conversation_id=conversation_id,
                run=attach_files_of,
                paths=held,
            )
        return delivered

    async def send_display_resource_for_conversation(
        self,
        *,
        conversation_id: UUID,
        request: DisplayResourceRequest,
        tool_call_id: str | None = None,
        tool_output: object | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Show one resource -- a file, a table, a card -- on the surface.

        ``request`` is the validated model. It used to also accept the raw dict
        and validate it here, which no caller ever needed: the tool path hands
        over a `DisplayResourceRequest` and always did. Only two tests used the
        dict, so the branch existed to be tested.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        display_request = request
        render_plan = build_display_resource_render_plan(
            pod_id=target.pod_id,
            request=display_request,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            tool_output=tool_output,
        )
        message_metadata = await self.delivery.egress_metadata(target, metadata)
        # A FILE resource is delivered as a native attachment when it fits the
        # platform's cap; otherwise we fall through to the card render plan, whose
        # action is a Lemma app deep link — openable only by a recipient with pod
        # access, so this fallback is a dead end for an outside contact. Which is
        # why that card carries the file's kind and size: it is the part a
        # recipient who cannot follow the link can still act on.
        if display_request.type is DisplayResourceType.FILE and display_request.path:
            resolved = await resolve_pod_file_parts(
                uow=self.uow,
                target=target,
                conversation_id=conversation_id,
                path=display_request.path,
                # No caption. The file name used to be one, and every platform
                # already prints it on the bubble — so the single line the
                # message could carry said what the reader could already see.
                caption=None,
                page_preview=True,
            )
            if resolved.facts.unreadable:
                # The read failed and said why in its own log line. A link card
                # for a file that could not be opened is a card the recipient
                # usually cannot open either, so report that it was not shown
                # and let the tool tell the model, instead of sending it.
                return False
            if resolved.files:
                # A PDF's page image and the document itself are one envelope,
                # so they arrive in that order rather than as two sends racing
                # to be first.
                #
                # The card goes with them as each file's fallback. A platform
                # that cannot attach at all -- Teams has no outbound file upload
                # -- would otherwise degrade to a line naming the file, which
                # tells the recipient less than the link card it replaced.
                return await self.delivery.deliver_envelope(
                    target,
                    envelope=SurfaceEnvelope(
                        files=[
                            item.model_copy(
                                update={
                                    "fallback": apply_file_facts(
                                        render_plan, resolved.facts
                                    )
                                }
                            )
                            for item in resolved.files
                        ]
                    ),
                    metadata=message_metadata,
                    conversation_id=conversation_id,
                )
            render_plan = apply_file_facts(render_plan, resolved.facts)
        elif display_request.type is DisplayResourceType.TABLE:
            render_plan = apply_table_rows(
                render_plan,
                await resolve_table_preview(
                    uow=self.uow,
                    target=target,
                    conversation_id=conversation_id,
                    request=display_request,
                ),
            )
        return await self.delivery.deliver_envelope(
            target,
            envelope=SurfaceEnvelope(resources=[render_plan]),
            metadata=message_metadata,
            conversation_id=conversation_id,
        )

    async def send_questions_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
        narration: str | None = None,
        attach_files_of: RunFiles | None = None,
    ) -> bool:
        """Render the conversation's pending ``ask_user`` questions on its surface.

        Triggered by the WAITING run event. Reads the paused ask_user tool-call
        args, builds a render plan, and delivers it as native tappable choices
        where supported or a formatted text message otherwise. The user's answer
        is routed back via ``handle_interaction`` (native submit) or the
        typed-reply path in ``start_agent_chat``.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        request = await self._pending_ask_user(conversation_id)
        if target is None or request is None:
            logger.debug(
                "agent_surfaces.egress.ask_user_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        pending, validated = request
        plan = build_ask_user_render_plan(
            request=validated,
            conversation_id=conversation_id,
            tool_call_id=pending.tool_call_id or str(tool_call_id or ""),
        )
        files, held = await held_files_for_run(
            uow=self.uow,
            target=target,
            conversation_id=conversation_id,
            run=attach_files_of,
        )
        delivered = await self.delivery.deliver_envelope(
            target,
            # The lead-in and the question are one thing the person receives.
            # Sent as two, they arrive as two on a chat surface and as two
            # emails on a surface that only gets one.
            envelope=SurfaceEnvelope(text=narration, choices=plan, files=files),
            metadata=await self.delivery.egress_metadata(target),
            conversation_id=conversation_id,
        )
        if delivered and held and attach_files_of is not None:
            await release_held_files(
                uow=self.uow,
                conversation_id=conversation_id,
                run=attach_files_of,
                paths=held,
            )
        return delivered

    async def send_prompt_as_text_for_conversation(
        self,
        *,
        conversation_id: UUID,
        kind: str,
        tool_call_id: str | None = None,
    ) -> bool:
        """Ask a paused run's question or approval as a plain message.

        For a prompt whose native render did not arrive: the run is parked on an
        answer, and a person who was shown nothing cannot give one. The words
        say why the reply is typed, and the typed reply is then treated as the
        answer -- the same record a tapped "Other" leaves.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        if kind == "ask_user":
            request = await self._pending_ask_user(conversation_id)
            if request is None:
                return False
            pending, validated = request
            plan = build_ask_user_render_plan(
                request=validated,
                conversation_id=conversation_id,
                tool_call_id=pending.tool_call_id or str(tool_call_id or ""),
            )
            lead = "I need your answer to go on, but I couldn't show the buttons. Reply here with it."
        elif kind == "request_approval":
            pending = await agent_conversations.pending_approval(
                self.uow, conversation_id
            )
            if pending is None or not pending.is_approval:
                return False
            plan = await approval_plan(self.uow, pending, conversation_id, tool_call_id)
            lead = "I need your approval to go on, but I couldn't show the buttons. Reply here to answer."
        else:
            return False
        delivered = await self.delivery.deliver_envelope(
            target,
            envelope=SurfaceEnvelope(text=f"{lead}\n\n{plan.to_plain_text()}"),
            metadata=await self.delivery.egress_metadata(target),
            conversation_id=conversation_id,
        )
        if delivered:
            await remember_free_text_answer_wanted(
                self.uow,
                conversation_id=conversation_id,
                tool_call_id=tool_call_id_of(plan),
            )
        return delivered

    async def _pending_ask_user(
        self, conversation_id: UUID
    ) -> tuple[PendingInteraction, AskUserRequest] | None:
        """The paused ``ask_user`` call and its validated request, or nothing."""
        pending = await agent_conversations.pending_question(self.uow, conversation_id)
        if pending is None:
            return None
        raw_request = ask_user_request_dict(pending.tool_args)
        if raw_request is None:
            return None
        try:
            request = AskUserRequest.model_validate(raw_request)
        except ValidationError:
            # Stored tool_args that will not validate is a bug in whatever wrote
            # them, not a transient — and the question is dropped here.
            logger.warning(
                "agent_surfaces.egress.ask_user_render_skipped.degraded",
                conversation_id=conversation_id,
                exc_info=True,
            )
            return None
        return (pending, request) if request.questions else None

    async def send_sign_in_prompt_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
        narration: str | None = None,
    ) -> bool:
        """A link to the site their agent is stuck at. See `surface_sign_in`.

        False when this conversation has no surface: a web-only one has the
        browser pane beside it, so there is nothing to deliver.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        envelope = (
            None
            if target is None
            else await sign_in_prompt_envelope(
                self.uow,
                conversation_id=conversation_id,
                tool_call_id=tool_call_id,
                narration=narration,
            )
        )
        if target is None or envelope is None:
            logger.debug(
                "agent_surfaces.egress.sign_in_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        return await self.delivery.deliver_envelope(
            target,
            envelope=envelope,
            metadata=await self.delivery.egress_metadata(target),
            conversation_id=conversation_id,
        )

    async def send_approval_prompt_for_conversation(
        self,
        *,
        conversation_id: UUID,
        tool_call_id: str | None = None,
        narration: str | None = None,
        attach_files_of: RunFiles | None = None,
    ) -> bool:
        """Render a pending ``request_approval`` on the surface.

        Delivers native Approve/Deny buttons where supported (the tapped decision
        routes back via ``handle_interaction``); on any platform without native
        buttons, or if the native render fails, falls back to a text prompt the
        user answers "approve"/"deny" (routed back by the typed-reply path in
        ``start_agent_chat`` via ``maybe_resume_pending_interaction``). Never
        swallowed.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        # The approval pause specifically, not "whatever this conversation is
        # waiting on". `get_pending_user_interaction` answers the second, across
        # every pausing tool, and the check below then threw away anything that
        # was not an approval — so a single `ask_user` nobody ever tapped, being
        # older, shadowed every approval that followed it in that conversation
        # for good. On a chat surface, where one conversation stands for the
        # whole relationship with a person, that is permanent: dev's standing
        # Telegram chat stopped rendering approval cards entirely.
        pending = await agent_conversations.pending_approval(self.uow, conversation_id)
        if target is None or pending is None or not pending.is_approval:
            logger.debug(
                "agent_surfaces.egress.approval_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        # Native buttons, then a text prompt, then admit it reached nobody.
        files, held = await held_files_for_run(
            uow=self.uow,
            target=target,
            conversation_id=conversation_id,
            run=attach_files_of,
        )
        delivered = await self.delivery.deliver_envelope(
            target,
            envelope=SurfaceEnvelope(
                text=narration,
                decision=await approval_plan(
                    self.uow, pending, conversation_id, tool_call_id
                ),
                files=files,
            ),
            metadata=await self.delivery.egress_metadata(target),
            conversation_id=conversation_id,
        )
        if delivered and held and attach_files_of is not None:
            await release_held_files(
                uow=self.uow,
                conversation_id=conversation_id,
                run=attach_files_of,
                paths=held,
            )
        return delivered

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
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        # The caption is model-authored — strip any reasoning before delivery.
        caption = sanitize_user_visible_text(caption) if caption else caption
        loaded = await load_pod_file_bytes(
            uow=self.uow,
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

        # Voice note, then the same bytes as an attachment (an audio player on
        # most platforms), then the link card. Three rungs that used to be
        # written out here; the envelope walks them.
        return await self.delivery.deliver_envelope(
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
            metadata=await self.delivery.egress_metadata(target),
            conversation_id=conversation_id,
        )
