"""Private, resumable signup before surface routing and conversation ingestion."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.services.onboarding_inputs import command_of
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    PrivateDeliveryUnavailable,
    private_onboarding_destination,
)
from app.modules.agent_surfaces.services.onboarding_replies import (
    initial_prompt_for,
    room_notice,
)
from app.modules.agent_surfaces.services.onboarding_sender import (
    create_pending,
    recognize_sender,
)
from app.modules.agent_surfaces.services.onboarding_settle import OnboardingSettle
from app.modules.agent_surfaces.services.onboarding_submissions import (
    SUBMISSION_ID_KEY,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
    resolve_onboarding_transport,
)
from app.modules.agent_surfaces.services.personal_dm_routes import (
    PersonalRouteUnavailable,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    RateLimitExceeded,
    hold_chat_onboarding,
)

logger = get_logger(__name__)

#: The one reply a crashed step gets. Before the safety net it got none: the
#: exception dead-lettered after its retries and the person, who had just sent
#: a code or a choice, saw nothing at all.
STEP_CRASHED_MESSAGE = (
    "Something went wrong on our side while setting up your account. Send your "
    "last message again, or reply `cancel` to start over."
)

#: Said in front of the first prompt when an expired signup restarts itself.
RESTARTED_NOTE = "Your earlier setup expired, so let's start again."


class ChatOnboardingCoordinator(OnboardingSettle):
    """Runs one inbound message through the signup it belongs to, if any."""

    async def handle(self, request: SurfaceIngressRequest) -> OnboardingIngressResult:
        transport = await resolve_onboarding_transport(
            request, uow_factory=self._uows, adapters=self._adapters
        )
        if transport is None:
            return OnboardingIngressResult(False)
        async with hold_chat_onboarding(transport.binding_key) as lease:
            result = await self._handle_once(transport)
            await lease.require_ownership()
            return result

    async def _handle_once(
        self, transport: OnboardingTransport
    ) -> OnboardingIngressResult:
        """Run the message once, however many times the platform delivers it.

        The claim is kept only when onboarding consumed the message itself. A
        result that hands it on -- to ordinary ingestion, or with a context to
        run -- gives the claim back, because what it hands to takes claims of
        its own, and a raise gives it back so the inbox's retry is not read as
        a duplicate. What it buys: a redelivered "here is my email" no longer
        sends a second code email, and a redelivered code is not read twice.
        """
        event = transport.event
        # A native form submission arrives as the stored destination, which
        # carries the id of the message that opened the chat; its own id rides
        # in the metadata.
        message_id = event.metadata.get(SUBMISSION_ID_KEY) or event.external_message_id
        claim = {
            "platform": event.platform.value,
            "binding_key": transport.binding_key,
            "external_message_id": str(message_id) if message_id else None,
        }
        if not await self._event_dedup_store.claim_onboarding_message(**claim):
            return OnboardingIngressResult(True)
        kept = False
        try:
            result = await self._advance_safely(transport)
            kept = result.handled and result.context is None
        finally:
            if not kept:
                await self._event_dedup_store.release_onboarding_message(**claim)
        return result

    async def _advance_safely(
        self, transport: OnboardingTransport
    ) -> OnboardingIngressResult:
        """`_advance`, with one reply when a step fails in a way nobody handled.

        The two delivery failures are re-raised: they mean the private chat
        itself is unavailable, and their callers answer them. Anything else is a
        bug or an outage in the middle of a step, and the person is owed a
        sentence rather than silence. If even that sentence cannot be sent, the
        failure goes back to the inbox to be retried -- and the dedup claim with
        it, so the retry is not mistaken for a duplicate.
        """
        try:
            return await self._advance(transport)
        except PersonalRouteUnavailable, PrivateDeliveryUnavailable:
            raise
        except Exception as crash:  # noqa: BLE001 - the safety net is the point
            logger.error(
                "agent_surfaces.chat_onboarding.step_crashed.failed",
                platform=transport.event.platform.value,
                error_type=type(crash).__name__,
                exc_info=True,
            )
            if not transport.event.is_dm:
                raise
            await self._reply(transport, transport.event, STEP_CRASHED_MESSAGE)
            return OnboardingIngressResult(True)

    async def _advance(self, transport: OnboardingTransport) -> OnboardingIngressResult:
        event = transport.event
        state = await self._state(transport.binding_key)
        if (
            state is None
            or state.handed_off_at is not None
            or state.step == OnboardingStep.READY
        ):
            # READY joins the handed-off case because by then the workspace,
            # the verified identity and the route all exist -- everything the
            # recognised-sender path needs. Only the replay of the *original*
            # request is still outstanding, and it runs on its own event.
            started = await recognize_sender(
                self._uows,
                transport,
                adapters=self._adapters,
                event_dedup_store=self._event_dedup_store,
            )
            if isinstance(started, OnboardingIngressResult):
                return started
            if started.step == OnboardingStep.VERIFIED:
                # Recognised, with nowhere to talk. The message that got them
                # here is their request, held for replay -- not an answer to a
                # question nobody has asked yet. Reading it as one is how "2"
                # attached workspace two and "New York trip" made a pod.
                destination = ParsedInboundSurfaceEvent.model_validate(
                    started.destination
                )
                await self._settle(transport, started, destination)
                return OnboardingIngressResult(True)
            state = started
        if state.step == OnboardingStep.HANDOFF:
            return await self._handoff(transport, state)
        destination = ParsedInboundSurfaceEvent.model_validate(state.destination)
        if not event.is_dm:
            # Once handed off, only private submissions can advance signup.
            await room_notice(self._adapters, transport)
            return OnboardingIngressResult(True)
        if command_of(event.message_text) == "cancel":
            await self._outcomes.cancelled(transport, state, destination)
            return OnboardingIngressResult(True)
        if state.expires_at <= datetime.now(timezone.utc) and state.user_id is None:
            return await self._restart(transport, state)
        if state.step == OnboardingStep.AWAITING_PHONE:
            return await self._contact(transport, state, destination)
        return await self._step(transport, state, destination)

    async def _restart(
        self, transport: OnboardingTransport, state: PendingState
    ) -> OnboardingIngressResult:
        """An expired signup, met by a new message: start again *with* that message.

        This used to say "Setup expired. Send a fresh request" and throw the
        message away -- so the person's next message was the fresh request, and
        the one they had actually sent was gone. Now the expired row is closed
        (its code revoked) and a new signup opens holding this message for
        replay, which is what they would have done by hand.
        """
        await self._outcomes.revoke_code(state, transport.event.platform.value)
        fresh = await create_pending(self._uows, transport, transport.event)
        return await self._handoff(transport, fresh, note=RESTARTED_NOTE)

    async def _step(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        """Run the step this signup is actually on.

        Split out of `_advance` so that the dispatch and the guards that decide
        whether to dispatch at all stay separately readable -- and so that
        adding a step does not push one function past the complexity ceiling.
        """
        try:
            if state.step == OnboardingStep.AWAITING_EMAIL:
                if not self._can_email():
                    return await self._no_email(transport, state, destination)
                return await self._email(transport, state, destination)
            if state.step == OnboardingStep.AWAITING_CODE:
                return await self._code(transport, state, destination)
            if state.step == OnboardingStep.AWAITING_POD:
                return await self._pod(transport, state, destination)
            if state.user_id is not None:
                await self._settle(transport, state, destination)
        except ChallengeRejected as error:
            # Only the challenge service's refusals reach here, and every one of
            # them is about the answer just given: a wrong code, an expired one,
            # a resend asked for too soon. The step is the question they are
            # still on, so saying so and staying put is the whole answer. The
            # permanent refusals are caught where they arise -- `_settle`,
            # `_pod` and `_account_refused` -- because only there is it known
            # that no later message can help.
            logger.info(
                "agent_surfaces.chat_onboarding.answer_rejected.observed",
                platform=state.platform,
                step=state.step,
                reason=error.message,
            )
            await self._reply(transport, destination, error.message, step=state.step)
        except RateLimitExceeded:
            await self._reply(
                transport,
                destination,
                "Too many code requests. Try again later.",
                step=state.step,
            )
        return OnboardingIngressResult(True)

    async def _handoff(
        self, transport: OnboardingTransport, state: PendingState, *, note: str = ""
    ) -> OnboardingIngressResult:
        event = transport.event
        destination = await private_onboarding_destination(
            event, credentials=transport.credentials
        )
        if state.expires_at <= datetime.now(timezone.utc):
            return await self._restart(transport, state)
        if state.original_event is not None:
            original = ParsedInboundSurfaceEvent.model_validate(state.original_event)
            destination = destination.model_copy(
                update={"sender_email": original.sender_email}
            )
        step = (
            OnboardingStep.AWAITING_PHONE
            if event.platform == SurfacePlatform.TELEGRAM
            else OnboardingStep.AWAITING_EMAIL
        )
        prompt = initial_prompt_for(step)
        message = f"{note}\n\n{prompt}" if note else prompt
        # Retry private delivery before accepting any signup submission: the
        # shape every other advancing step now shares.
        await self._advance_if_delivered(
            state,
            {"destination": destination.model_dump(mode="json"), "step": step},
            lambda: self._reply(transport, destination, message, step=step),
        )
        return OnboardingIngressResult(True)
