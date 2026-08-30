"""Native interaction submissions -- a button press, a picked option.

These resume a run that paused on ``ask_user`` or ``request_approval``, which is
a different lifecycle from an ordinary inbound message: there is already a
conversation and a waiting run, and the work is matching the submission to it.
"""

from __future__ import annotations

from typing import Any


from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service

from app.modules.agent.contracts import AgentRunApprovalDecision
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.services.free_text_answer import (
    remember_free_text_answer_wanted,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    merge_other_answers,
)
from app.modules.agent_surfaces.services.interaction_helpers import (
    interaction_sender_matches,
    parse_interaction_target,
    resolve_current_interaction_delivery,
    resolve_interaction_delivery,
    retry_interaction_conversation,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


class SurfaceInteractionMixin:
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
        async with connection_released(self.uow.session):
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
                async with connection_released(self.uow.session):
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
                # Remember that they asked to type the answer. Without this the
                # next message is indistinguishable from any other, and the only
                # way to honour "Other" was to treat *every* typed message as an
                # answer — which is how an unanswered question came to swallow
                # whatever somebody said next. Recorded against the specific
                # call, so it cannot be spent on a later, unrelated one.
                await remember_free_text_answer_wanted(
                    self.uow,
                    conversation_id=conversation_id,
                    tool_call_id=tool_call_id,
                )
                async with connection_released(self.uow.session):
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
                    "agent_surfaces.ingress_service.surface_answer_submission_rejected_submitter.diagnostic",
                    external_user_id=parsed.external_user_id,
                    conversation_id=conversation_id,
                )
                return

            conversation = await self.conversation_service.conversation_repository.get_conversation(
                conversation_id
            )
            if conversation is None:
                logger.debug(
                    "agent_surfaces.ingress_service.surface_interaction_dropped_conversation_not.diagnostic",
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
                    async with connection_released(self.uow.session):
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
                async with connection_released(self.uow.session):
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
            async with connection_released(self.uow.session):
                await adapter.acknowledge_interaction(
                    credentials=credentials,
                    interaction=parsed,
                    text="Done",
                    clear_actions=True,
                )
        except Exception:
            if adapter is not None and credentials is not None:
                async with connection_released(self.uow.session):
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
        except TypeError, ValueError:
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
