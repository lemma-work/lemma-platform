"""Putting a message onto a surface, whichever way it was asked for.

Everything here answers the same question -- given a conversation and something
to say, which surface, adapter and thread does it go to, and in what shape does
that platform accept it. Split from :mod:`ingress_service` because it is the
outbound half: nothing here reads an inbound event.
"""

from __future__ import annotations

from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)

from typing import Any
from uuid import UUID


from app.core.infrastructure.db.transaction_locks import connection_released

from app.modules.agent.contracts import (
    AskUserRequest,
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent_surfaces.services.display_resource_content import (
    apply_file_facts,
    apply_table_rows,
    deliver_pod_file,
    load_pod_file_bytes,
    resolve_table_preview,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.errors import AgentSurfaceError
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
)
from app.modules.agent_surfaces.domain.ports import (
    ColdEmailThread,
)
from app.modules.agent_surfaces.services.cold_email_thread import (
    build_cold_email_thread,
)
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    # Re-exported: ``_ask_user_request_dict`` still has a caller here (the
    # native-interaction path) and a unit test that imports it from this module.
    _ask_user_request_dict,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
    build_ask_user_render_plan,
    build_display_resource_render_plan,
)
from app.core.log.log import get_logger

from app.modules.agent_surfaces.services.surface_egress_target import (
    SurfaceEgressTargetMixin,
)

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


def _approval_plan(
    pending: dict[str, Any], conversation_id: UUID, tool_call_id: str | None
) -> Any:
    """The approval card for a paused ``request_approval`` call."""
    tool_args = pending.get("tool_args") or {}
    # An approve-for-session button only makes sense when the paused call
    # carries a real permission gate (it lets the exact action skip future
    # prompts); otherwise it is noise.
    permission_ids = tool_args.get("permission_ids")
    return build_approval_render_plan(
        conversation_id=conversation_id,
        tool_call_id=str(pending.get("tool_call_id") or tool_call_id or ""),
        title=str(tool_args.get("title") or "Action requires your approval"),
        reason=str(tool_args.get("reason") or "") or None,
        tool_name=str(tool_args.get("tool_name") or "") or None,
        allow_session=bool(isinstance(permission_ids, list) and permission_ids),
    )


class SurfaceEgressMixin(SurfaceEgressTargetMixin):
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
        # Every identity they hold on this platform, not just the most recently
        # seen one: Slack ids are per workspace and Teams ids per tenant, so
        # taking one made a pod's second workspace unreachable. The surface's own
        # tenant narrows the list, permissively where none was ever recorded.
        identities = await external_user_repository.list_by_resolved_users(
            platform=surface.surface_type.value, resolved_user_ids=[user_id]
        )
        for ext in identities:
            if not ext.external_user_id or not surface.matches_tenant(ext.tenant_id):
                continue
            link = await self.conversation_link_repository.get_latest_by_surface_and_external_user(
                surface_id=surface.id, external_user_id=ext.external_user_id
            )
            if link is not None:
                return await self.send_agent_message_for_conversation(
                    conversation_id=link.conversation_id, message=message
                )
        return False

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

        Cannot reuse ``_resolve_egress_target``: that resolves a *stored link*,
        and the whole point of a cold open is that there is not one yet. Returns
        None when the surface is inactive, has no adapter, or sits on a platform
        that cannot start a thread — all of which are "no", not failures.
        """
        if not surface.is_active:
            return None
        adapter = self.adapter_registry.get(surface.surface_type)
        if adapter is None:
            return None
        clean_message = sanitize_user_visible_text(message)
        if not clean_message:
            return None
        credentials = await self._resolve_credentials(surface)
        sent = await adapter.send_cold_email(
            credentials=credentials,
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
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(getattr(self.uow, "session", None)):
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
        # platform's cap; otherwise we fall through to the card render plan, whose
        # action is a Lemma app deep link — openable only by a recipient with pod
        # access, so this fallback is a dead end for an outside contact. Which is
        # why that card carries the file's kind and size: it is the part a
        # recipient who cannot follow the link can still act on.
        if display_request.type is DisplayResourceType.FILE and display_request.path:
            delivery = await deliver_pod_file(
                uow=self.uow,
                conversation_service=self.conversation_service,
                target=target,
                conversation_id=conversation_id,
                path=display_request.path,
                # No caption. The file name used to be one, and every platform
                # already prints it on the bubble — so the single line the
                # message could carry said what the reader could already see.
                caption=None,
                page_preview=True,
            )
            if delivery.delivered:
                return True
            render_plan = apply_file_facts(render_plan, delivery)
        elif display_request.type is DisplayResourceType.TABLE:
            render_plan = apply_table_rows(
                render_plan,
                await resolve_table_preview(
                    uow=self.uow,
                    conversation_service=self.conversation_service,
                    target=target,
                    conversation_id=conversation_id,
                    request=display_request,
                ),
            )
        message_metadata = await self._egress_metadata_with_agent_name(target, metadata)
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(getattr(self.uow, "session", None)):
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
                "agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic",
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
                "agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        raw_request = _ask_user_request_dict(pending.get("tool_args"))
        if raw_request is None:
            pending.get("tool_args")
            logger.debug(
                "agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        try:
            request = AskUserRequest.model_validate(raw_request)
        except Exception:
            logger.debug(
                "agent_surfaces.ingress_service.surface_ask_user_render_skipped.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        if not request.questions:
            logger.debug(
                "agent_surfaces.ingress_service.surface_ask_user_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        plan = build_ask_user_render_plan(
            request=request,
            conversation_id=conversation_id,
            tool_call_id=str(pending.get("tool_call_id") or tool_call_id or ""),
        )
        return await self._deliver_envelope(
            target,
            envelope=SurfaceEnvelope(choices=plan),
            metadata=await self._egress_metadata_with_agent_name(target, None),
            conversation_id=conversation_id,
        )

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
                "agent_surfaces.ingress_service.surface_request_approval_not_delivered.diagnostic",
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
                "agent_surfaces.ingress_service.surface_request_approval_not_delivered.diagnostic",
                conversation_id=conversation_id,
            )
            return False

        return await self._deliver_approval(
            target,
            plan=_approval_plan(pending, conversation_id, tool_call_id),
            metadata=await self._egress_metadata_with_agent_name(target, None),
            conversation_id=conversation_id,
        )

    async def _deliver_approval(
        self,
        target: SurfaceEgressTarget,
        *,
        plan: Any,
        metadata: dict[str, Any],
        conversation_id: UUID,
    ) -> bool:
        """Native buttons, then a text prompt, then admit it reached nobody."""
        return await self._deliver_envelope(
            target,
            envelope=SurfaceEnvelope(decision=plan),
            metadata=metadata,
            conversation_id=conversation_id,
        )

    async def _deliver_envelope(
        self,
        target: SurfaceEgressTarget,
        *,
        envelope: SurfaceEnvelope,
        metadata: dict[str, Any],
        conversation_id: UUID,
    ) -> bool:
        """Hand one envelope to the platform and say whether it arrived.

        The ladder -- native, then the part's own text, then nothing -- lives in
        ``BaseSurfaceAdapter.deliver`` now, and this is what is left once the two
        hand-written copies of it are gone: resolve a target, release the
        connection, report.

        Returning ``False`` matters as much as delivering. A prompt that reached
        nobody leaves the run WAITING on an answer that cannot come, so the
        caller un-dedupes and a later WAITING event tries again.
        """
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(getattr(self.uow, "session", None)):
            try:
                receipt = await target.adapter.deliver(
                    credentials=target.credentials,
                    event=target.event,
                    envelope=envelope,
                    metadata=metadata,
                )
            except AgentSurfaceError:
                logger.warning(
                    "agent_surfaces.egress.envelope_reached_nobody.degraded",
                    conversation_id=str(conversation_id),
                    platform=target.surface.surface_type.value,
                )
                return False
            if receipt.degraded:
                logger.debug(
                    "agent_surfaces.egress.envelope_degraded.diagnostic",
                    conversation_id=str(conversation_id),
                    platform=target.surface.surface_type.value,
                    parts=receipt.degraded,
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
        # The caption is model-authored — strip any reasoning before delivery.
        caption = sanitize_user_visible_text(caption) if caption else caption
        loaded = await load_pod_file_bytes(
            uow=self.uow,
            conversation_service=self.conversation_service,
            target=target,
            conversation_id=conversation_id,
            path=path,
        )
        if loaded is None:
            logger.debug(
                "agent_surfaces.ingress_service.surface_voice_note_fetch_conversation.diagnostic",
                conversation_id=conversation_id,
            )
            return False
        entity, content = loaded

        mime = entity.mime_type or "audio/ogg"
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(getattr(self.uow, "session", None)):
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
                    "agent_surfaces.ingress_service.surface_voice_note_send_conversation.diagnostic",
                    conversation_id=conversation_id,
                )
            # Fallback: native file attachment (audio player), then a link card.
            attached = await deliver_pod_file(
                uow=self.uow,
                conversation_service=self.conversation_service,
                target=target,
                conversation_id=conversation_id,
                path=path,
                caption=caption,
            )
            if attached.delivered:
                return True
            return await self.send_display_resource_for_conversation(
                conversation_id=conversation_id,
                request=DisplayResourceRequest(
                    type=DisplayResourceType.FILE, path=path
                ),
            )

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
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(getattr(self.uow, "session", None)):
            await target.adapter.add_processing_indicator(
                credentials=target.credentials,
                event=target.event,
                metadata=indicator_metadata,
            )
            return True
