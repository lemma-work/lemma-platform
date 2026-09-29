"""Native interaction submissions -- a button press, a picked option.

These resume a run that paused on ``ask_user`` or ``request_approval``, which is
a different lifecycle from an ordinary inbound message: there is already a
conversation and a waiting run, and the work is matching the submission to it.

Every path out of ``handle_interaction`` that has a delivery target says what
happened -- accepted, expired, not yours, gone, failed. A tap that produces
nothing is indistinguishable from a broken button, so the person taps again;
and the one path that used to produce nothing was the failure path, on the
three platforms where ``acknowledge_interaction`` was a no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service

from app.modules.agent.contracts import AgentRunApprovalDecision
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.conversations_for_surfaces import SurfaceConversation
from app.modules.agent_surfaces.domain.entities import (
    platform_value_for_source,
    AgentSurfaceConversationLink,
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
    ResolvedSurfaceUser,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfaceEventDedupStorePort,
    SurfaceInstallationRepositoryPort,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_link_repository import (  # noqa: E501
    SurfaceConversationLinkRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
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
    InteractionDelivery,
    interaction_sender_matches,
    parse_interaction_target,
    resolve_current_interaction_delivery,
    resolve_interaction_delivery,
)
from app.core.log.log import get_logger

from app.modules.agent_surfaces.services.conversation_binder import ConversationBinder
from app.modules.agent_surfaces.services.surface_router import SurfaceRouter

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.


def _authorized_surface_ids(request) -> list[UUID] | None:
    """The surfaces this verified request may act on, or `None` for all of them.

    A surface-addressed webhook proved exactly one. A platform webhook proved
    whatever `receiver_surface_ids` names -- for Slack and Teams that is derived
    from the workspace the signature belongs to, which is the boundary a forged
    payload cannot cross. `None` survives only where the secret was ours.
    """
    if isinstance(request, SurfaceDirectWebhookIngress):
        return [request.surface_id]
    return request.receiver_surface_ids


@dataclass
class _InteractionAttempt:
    """What one submission has done so far, for the failure path to read.

    A failure has to know how far the tap got: before the claim there is nothing
    to give back, after the answer reached the run there is nothing to undo.
    """

    delivery: InteractionDelivery | None = None
    #: The replay claim is held, and should be handed back if the work fails.
    claimed: bool = False
    #: The answer (or the retry) reached the run, so a repeat must stay a repeat.
    applied: bool = False


class SurfaceInteractionMixin:
    #: Supplied by `AgentSurfaceIngressService`; see `SurfaceInboundMixin`.
    uow: SqlAlchemyUnitOfWork
    router: SurfaceRouter
    binder: ConversationBinder
    #: What `interaction_helpers.InteractionIngress` reads off this object, plus
    #: the dedup store -- declared so the mixin can be passed as one.
    surface_repository: SurfaceInstallationRepositoryPort
    conversation_link_repository: SurfaceConversationLinkRepository
    credential_resolver: SurfaceCredentialResolver
    adapter_registry: SurfacePlatformAdapterRegistry
    event_dedup_store: SurfaceEventDedupStorePort

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
            platform = platform_value_for_source(request.source)
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
                credentials = await self.credential_resolver.for_surface(surface)
                async with connection_released(self.uow.session):
                    await adapter.acknowledge_interaction(
                        credentials=credentials,
                        interaction=parsed,
                        text="This button expired. Just reply here with your answer, or ask again.",
                        show_alert=True,
                        clear_actions=True,
                    )
            return True
        await self.handle_interaction(
            parsed, authorized_surface_ids=_authorized_surface_ids(request)
        )
        return True

    async def handle_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        *,
        authorized_surface_ids: list[UUID] | None = None,
    ) -> None:
        """Resume a paused ``ask_user`` run from a native answer submission.

        ``authorized_surface_ids`` is what the signature actually proved, and it
        is the difference between resolving an interaction and resolving
        *anybody's* interaction. The button carries an unsigned
        ``conversation_id|tool_call_id``, so without it the id alone decided
        whose conversation was reached. Defaulted to ``None`` for the callers
        that genuinely have no receiver list -- see `_within_authorized_scope`.

        The submitted values are keyed by question header (the native render uses
        the header as each input's id), so they map straight into
        ``AskUserResponse.answers`` and resume through the approval path — the
        agent receives a proper structured answer, not a plain message. Best
        effort; never raises to the caller.
        """
        attempt = _InteractionAttempt()
        try:
            await self._process_interaction(
                parsed, attempt, authorized_surface_ids=authorized_surface_ids
            )
        except Exception:
            # Said first, at a level `LOG_LEVEL=INFO` keeps. This handler used to
            # swallow the failure whole: the person got "I couldn't complete
            # that action" and nobody else got anything, so a button that broke
            # every time looked like a person who had not tapped it.
            logger.warning(
                "agent_surfaces.ingress_service.surface_interaction_failed.degraded",
                action=parsed.action,
                platform=str(parsed.platform),
                conversation_id=(
                    str(attempt.delivery[0].conversation_id)
                    if attempt.delivery
                    else None
                ),
                exc_info=True,
            )
            await self._recover_from_failed_interaction(parsed, attempt)

    async def _process_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        attempt: _InteractionAttempt,
        *,
        authorized_surface_ids: list[UUID] | None,
    ) -> None:
        located = await self._locate_interaction(
            parsed, authorized_surface_ids=authorized_surface_ids
        )
        # No delivery target means this tap was not provably ours to answer --
        # an unparseable callback, a conversation on another tenant's surface --
        # and there is nobody it would be safe to tell.
        if located is None:
            return
        delivery, tool_call_id = located
        attempt.delivery = delivery
        link, surface, _adapter, _credentials = delivery

        if parsed.interaction_state == "other":
            # Remember that they asked to type the answer. Without this the
            # next message is indistinguishable from any other, and the only
            # way to honour "Other" was to treat *every* typed message as an
            # answer — which is how an unanswered question came to swallow
            # whatever somebody said next. Recorded against the specific
            # call, so it cannot be spent on a later, unrelated one.
            await remember_free_text_answer_wanted(
                self.uow,
                conversation_id=link.conversation_id,
                tool_call_id=tool_call_id,
            )
            await self._acknowledge(
                parsed, delivery, text="Reply with your own answer."
            )
            return

        # Authz: only the surface user who owns the conversation may submit
        # the answer that was shown to them. Ahead of the replay claim, so a
        # refused tap cannot spend the claim a legitimate one would need.
        if not interaction_sender_matches(link, parsed):
            # Warning, not debug: this is the control in front of a native
            # Approve, and it now also fires where neither side identified
            # anybody. An operator has to be able to see that happening --
            # `LOG_LEVEL=INFO` drops debug before it is formatted.
            logger.warning(
                "agent_surfaces.ingress_service.interaction_submitter_refused.degraded",
                external_user_id=parsed.external_user_id,
                conversation_id=link.conversation_id,
            )
            # Say so, and say which of the two it is. Refusing in silence is
            # indistinguishable from the button being broken, and "not yours
            # to answer" is wrong when the truth is that nothing identified
            # the person who tapped. Either way the typed reply still works,
            # so the sentence has to point at it.
            await self._acknowledge(
                parsed,
                delivery,
                text=(
                    "I can't tell that this is yours to answer. Reply "
                    "with your decision instead and I'll take it."
                ),
                show_alert=True,
            )
            return

        # Replay protection: each submission is processed once. A repeat is an
        # expected double-tap or a redelivery -- the first delivery already told
        # the person what happened -- so it is dropped, at debug.
        #
        # Taken only now, with everything that can refuse the tap behind it, and
        # given back if the work then fails (see `_recover_from_failed_interaction`):
        # the claim used to be spent first, so a failure left the person's next
        # tap looking like a replay and being ignored in silence.
        #
        # The connection goes back for it. This is a Redis round trip in the
        # middle of the interaction path, and everything above it has only
        # read -- the writes start below, so `safe_to_release` genuinely
        # releases here rather than quietly declining.
        async with connection_released(self.uow.session):
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
                conversation_id=link.conversation_id,
                dedup_id=parsed.dedup_id,
            )
            return
        attempt.claimed = True

        conversation = await agent_conversations.surface_conversation(
            self.uow, link.conversation_id
        )
        if conversation is None:
            logger.debug(
                "agent_surfaces.ingress_service.surface_interaction_dropped_conversation_not.diagnostic",
                conversation_id=link.conversation_id,
            )
            await self._acknowledge(
                parsed,
                delivery,
                text="That conversation is gone. Please ask again.",
                show_alert=True,
                clear_actions=True,
            )
            return

        if parsed.action == "retry":
            await self._retry_interaction(parsed, attempt, delivery, conversation)
            return
        await self._resolve_interaction(
            parsed, attempt, delivery, conversation, tool_call_id=tool_call_id
        )

    async def _locate_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        *,
        authorized_surface_ids: list[UUID] | None,
    ) -> tuple[InteractionDelivery, str] | None:
        """Match a submission to the thread it belongs to, or None for "not ours"."""
        if parsed.action == "retry":
            current = await resolve_current_interaction_delivery(
                self, parsed, authorized_surface_ids=authorized_surface_ids
            )
            return (current, "") if current is not None else None
        target = parse_interaction_target(parsed)
        if target is None:
            return None
        conversation_id, tool_call_id = target
        delivery = await resolve_interaction_delivery(
            self,
            parsed,
            conversation_id,
            authorized_surface_ids=authorized_surface_ids,
        )
        return (delivery, tool_call_id) if delivery is not None else None

    async def _acknowledge(
        self,
        parsed: ParsedSurfaceInteraction,
        delivery: InteractionDelivery,
        *,
        text: str,
        **options: bool,
    ) -> None:
        _link, _surface, adapter, credentials = delivery
        async with connection_released(self.uow.session):
            await adapter.acknowledge_interaction(
                credentials=credentials,
                interaction=parsed,
                text=text,
                **options,
            )

    async def _retry_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        attempt: _InteractionAttempt,
        delivery: InteractionDelivery,
        conversation: SurfaceConversation,
    ) -> None:
        link, surface, _adapter, _credentials = delivery
        refreshed = await self._refresh_interaction_conversation(
            link=link,
            surface=surface,
            conversation=conversation,
        )
        if refreshed is None:
            await self._acknowledge(
                parsed,
                delivery,
                text="That conversation is gone. Please ask again.",
                show_alert=True,
                clear_actions=True,
            )
            return
        link, conversation, restarted = refreshed
        if restarted:
            await self._acknowledge(
                parsed,
                delivery,
                text="This chat started a new conversation. Send your message again.",
                show_alert=True,
                clear_actions=True,
            )
            return
        await agent_conversations.retry_failed_run(
            self.uow,
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )
        attempt.applied = True
        await self._acknowledge(parsed, delivery, text="Retrying…", clear_actions=True)

    async def _resolve_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        attempt: _InteractionAttempt,
        delivery: InteractionDelivery,
        conversation: SurfaceConversation,
        *,
        tool_call_id: str,
    ) -> None:
        # An approval button carries an explicit decision (approve / deny /
        # approve-for-session) with no answer payload; an ask_user submit
        # carries answers keyed by question header.
        if parsed.approval_decision is not None:
            decision = AgentRunApprovalDecision(parsed.approval_decision)
            response: dict[str, object] = {}
        else:
            decision = AgentRunApprovalDecision.APPROVE_ONCE
            response = {"answers": merge_other_answers(parsed.values)}
        auth_ctx = await create_authorization_data_service(self.uow).build_user_context(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            await agent_conversations.resolve_pending_interaction(
                self.uow,
                conversation_id=conversation.id,
                approval_id=tool_call_id,
                user_id=conversation.user_id,
                pod_id=conversation.pod_id,
                decision=decision,
                response=response,
            )
        finally:
            reset_current_context(token)
        attempt.applied = True
        await self._acknowledge(parsed, delivery, text="Done", clear_actions=True)

    async def _recover_from_failed_interaction(
        self,
        parsed: ParsedSurfaceInteraction,
        attempt: _InteractionAttempt,
    ) -> None:
        """Make a failed tap retryable, and tell the person it did not work.

        Runs inside the failure handler, so it must not raise: anything that goes
        wrong here is logged and left, or the failure it is recovering from would
        be replaced by a less useful one.

        Once the answer has reached the run (``applied``) nothing is undone and
        nothing is said -- a failed *acknowledgement* is not a failed action, and
        giving the claim back would let a second tap answer the same question
        twice.
        """
        if attempt.applied or attempt.delivery is None:
            return
        _link, surface, _adapter, _credentials = attempt.delivery

        async def hand_the_replay_claim_back() -> None:
            if attempt.claimed:
                await self.event_dedup_store.release_message(
                    surface_installation_id=surface.id,
                    platform=surface.surface_type,
                    external_channel_id=parsed.external_channel_id,
                    external_thread_id=parsed.external_thread_id,
                    external_message_id=parsed.dedup_id,
                )

        # Gathered rather than awaited in turn, so one step failing cannot stop
        # the other: a Redis outage must not leave the person without their
        # answer, nor a Slack one leave the claim spent.
        released, acknowledged = await asyncio.gather(
            hand_the_replay_claim_back(),
            self._acknowledge(
                parsed,
                attempt.delivery,
                text="I couldn’t complete that action.",
                show_alert=True,
            ),
            return_exceptions=True,
        )
        if isinstance(released, Exception):
            logger.warning(
                "agent_surfaces.ingress_service.surface_interaction_claim_release_failed.degraded",
                surface_id=str(surface.id),
                exc_info=released,
            )
        if isinstance(acknowledged, Exception):
            logger.warning(
                "agent_surfaces.ingress_service.surface_interaction_failure_unacknowledged.degraded",
                surface_id=str(surface.id),
                exc_info=acknowledged,
            )

    async def _refresh_interaction_conversation(
        self,
        *,
        link: AgentSurfaceConversationLink,
        surface: AgentSurfaceEntity,
        conversation: SurfaceConversation,
    ) -> tuple[AgentSurfaceConversationLink, SurfaceConversation, bool] | None:
        """Apply the normal DM agent/TTL reset policy before an action runs."""

        try:
            last_event = ParsedInboundSurfaceEvent.model_validate(link.last_event)
        except TypeError, ValueError:
            return link, conversation, False
        route = await self.router.resolve_route(surface=surface, parsed=last_event)
        if route is None:
            return link, conversation, False
        refreshed_link, _ = await self.binder.bind_conversation(
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
        refreshed_conversation = await agent_conversations.surface_conversation(
            self.uow, refreshed_link.conversation_id
        )
        if refreshed_conversation is None:
            return None
        return refreshed_link, refreshed_conversation, True
