"""Private, resumable signup before surface routing and conversation ingestion."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID


from app.core.config import settings
from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.log.log import get_logger
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    private_onboarding_destination,
)
from app.modules.agent_surfaces.services.onboarding_outcomes import (
    OnboardingOutcomes,
)
from app.modules.agent_surfaces.services.onboarding_replies import (
    initial_prompt_for,
    room_notice,
    say_privately,
)
from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    offer_text,
    read_choice,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
    resolve_onboarding_transport,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    RateLimitExceeded,
    EmailChallengeService,
    complete_chat_account,
    email_challenge_service,
    hold_chat_onboarding,
    parse_email_reply,
)
from app.modules.identity.contracts.surfaces import (
    live_user_ids_by_mobile_numbers,
)


from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.services.onboarding_sender import (
    read_state,
    recognize_sender,
    require_state,
)

logger = get_logger(__name__)

#: What "done" sounds like. Both completion paths used to say nothing at all:
#: the only sign of success was the replayed request coming back answered, and
#: `replay_onboarding` has several raises that dead-letter after their retries
#: -- so a failure there left somebody who had just proved their email with no
#: acknowledgement and no answer, and nothing to tell that apart from being
#: ignored. The confirmation is cheap and it is sent before the replay, so the
#: flow is never silent even when the replay is.
READY_MESSAGE = "You're all set. Picking up your message now."


def ready_message() -> str:
    """The confirmation, plus the one thing it never said.

    An account made here has a single login method and it is passwordless.
    Everything on the web that a person would reach for first -- a password,
    "forgot password", Continue with Google -- is refused for exactly that
    reason, and the only door that opens is a code sent to this same address.
    Nobody was ever told that, so signing up on WhatsApp and then trying the
    website looked like an account that did not work.

    Composed rather than folded into `READY_MESSAGE` so the constant stays the
    literal confirmation sentence that the recovery test matches on, and so the
    URL is read when the message is sent rather than when the module is
    imported.
    """
    return (
        f"{READY_MESSAGE}\n\n"
        f"To use Lemma on the web, go to {settings.frontend_url.rstrip('/')}/login "
        "and enter this same email address. We'll send you a sign-in code -- "
        "there's no password to remember."
    )


class ChatOnboardingCoordinator:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        challenges: EmailChallengeService | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
    ) -> None:
        self._uows = uow_factory
        self._adapters = SurfacePlatformAdapterRegistry()
        self._challenges = challenges
        self._event_dedup_store = event_dedup_store or get_surface_event_dedup_store()

    @property
    def _outcomes(self) -> OnboardingOutcomes:
        """The three ways a signup ends; see `onboarding_outcomes`."""
        return OnboardingOutcomes(
            uows=self._uows,
            adapters=self._adapters,
            challenge_service=self._challenge_service,
        )

    def _challenge_service(self, platform: str) -> EmailChallengeService:
        return self._challenges or email_challenge_service(platform)

    async def _state(self, binding_key: str) -> PendingState | None:
        return await read_state(self._uows, binding_key)

    async def _require_state(self, binding_key: str) -> PendingState:
        return await require_state(self._uows, binding_key)

    async def handle(self, request: SurfaceIngressRequest) -> OnboardingIngressResult:
        transport = await resolve_onboarding_transport(
            request, uow_factory=self._uows, adapters=self._adapters
        )
        if transport is None:
            return OnboardingIngressResult(False)
        async with hold_chat_onboarding(transport.binding_key) as lease:
            result = await self._advance(transport)
            await lease.require_ownership()
            return result

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
            # request is still outstanding, and it runs on its own event. What
            # used to happen here instead was that a message arriving in that
            # window re-ran provisioning and returned handled-with-no-context:
            # neither an answer nor a queue, so the message was simply dropped,
            # and the window widens whenever the worker is slow or retrying.
            started = await recognize_sender(
                self._uows,
                transport,
                adapters=self._adapters,
                event_dedup_store=self._event_dedup_store,
            )
            if isinstance(started, OnboardingIngressResult):
                return started
            state = started
        if state.step == OnboardingStep.HANDOFF:
            return await self._handoff(transport, state)
        destination = ParsedInboundSurfaceEvent.model_validate(state.destination)
        if not event.is_dm:
            # Once handed off, only private submissions can advance signup.
            return await self._room_notice(transport)
        if state.expires_at <= datetime.now(timezone.utc) and state.user_id is None:
            return await self._expire(transport, state, destination)
        text = event.message_text.strip()
        if text.lower() == "cancel":
            return await self._cancel(transport, state, destination)
        if state.step == OnboardingStep.AWAITING_PHONE:
            return await self._contact(transport, state, destination)
        return await self._step(transport, state, destination)

    async def _room_notice(
        self, transport: OnboardingTransport
    ) -> OnboardingIngressResult:
        """Answer the room; see `onboarding_replies.room_notice` for where."""
        await room_notice(self._adapters, transport)
        return OnboardingIngressResult(True)

    async def _cancel(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        await self._outcomes.cancelled(transport, state, destination)
        return OnboardingIngressResult(True)

    async def _expire(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        await self._outcomes.expired(transport, state, destination)
        return OnboardingIngressResult(True)

    async def _refused(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
        error: ChallengeRejected,
    ) -> None:
        await self._outcomes.refused(transport, state, destination, error)

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
                return await self._email(transport, state, destination)
            if state.step == OnboardingStep.AWAITING_CODE:
                return await self._code(transport, state, destination)
            if state.step == OnboardingStep.AWAITING_POD:
                return await self._pod(transport, state, destination)
            if state.user_id is not None:
                await self._complete(transport, state, destination)
        except ChallengeRejected as error:
            # Only the challenge service's refusals reach here, and every one of
            # them is about the answer just given: a wrong code, an expired one,
            # a resend asked for too soon. The step is the question they are
            # still on, so saying so and staying put is the whole answer. The
            # refusals that are *not* like that -- the ones with no next message
            # that could help -- are caught where they arise, in `_complete` and
            # `_pod`, because only there is it known that they are permanent.
            await self._reply(transport, destination, error.message, step=state.step)
        except RateLimitExceeded:
            await self._reply(
                transport,
                destination,
                "Too many code requests. Try again later.",
                step=state.step,
            )
        return OnboardingIngressResult(True)

    async def _pod(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        """Read which workspace they picked, and wire the chat to it.

        An unreadable answer re-asks rather than guesses: the cost of guessing
        is a conversation attached to somebody else's workspace.
        """
        from app.modules.agent_surfaces.services.onboarding_workspace import (
            attach_chosen_workspace,
        )

        choice = read_choice(transport.event.message_text, state.offered_pods)
        if choice is None:
            await self._reply(
                transport,
                destination,
                offer_text(state.offered_pods or []),
                step=OnboardingStep.AWAITING_POD,
            )
            return OnboardingIngressResult(True)
        try:
            refusal = await attach_chosen_workspace(
                self._uows, transport, state, choice
            )
        except ChallengeRejected as error:
            # `attach_chosen_workspace` records the identity first, so the two
            # permanent refusals reach here as well -- and a question is no
            # better an exit than a step: every workspace they could name is
            # refused by the same check, in the same words.
            await self._refused(transport, state, destination, error)
            return OnboardingIngressResult(True)
        # Success says so. The row is READY by now and the ready event is
        # published, so this reply is not guarded the way `_email` is: undoing
        # it is not available, and it does not need to be -- the replay the
        # event starts answers the person's original message either way, and a
        # failed confirmation only costs them the "all set" line.
        await self._reply(
            transport,
            destination,
            refusal if refusal is not None else ready_message(),
            step=OnboardingStep.AWAITING_POD if refusal is not None else None,
        )
        return OnboardingIngressResult(True)

    async def _write(self, state_id: UUID, values: dict[str, object]) -> None:
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state_id)
            assert row is not None
            for column, value in values.items():
                setattr(row, column, value)

    async def _advance_if_delivered(
        self,
        state: PendingState,
        advanced: dict[str, object],
        send: Callable[[], Awaitable[None]],
    ) -> None:
        """Move the row on, and move it back if the person is never told.

        Every step here writes before it sends, and it has to: the private
        destination and the step are what `native_prompt_metadata` reads to
        build the native form the message carries. So the write cannot wait for
        the send, and the only honest alternative is to undo it.

        Without that, a reply that failed left the row on a step the person had
        never been asked to answer -- sitting on AWAITING_CODE with no idea a
        code was wanted, their next message read as a wrong code. The inbox
        retries the delivery, and a retry is only worth anything if it re-runs
        the step from the top, prompt included.

        `finally` rather than `except` so a cancellation counts too.
        """
        before = {column: getattr(state, column) for column in advanced}
        await self._write(state.id, advanced)
        delivered = False
        try:
            await send()
            delivered = True
        finally:
            if not delivered:
                await self._write(state.id, before)

    async def _reply(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        message: str,
        *,
        step: str | None = None,
    ) -> None:
        await say_privately(
            self._adapters, self._uows, transport, destination, message, step=step
        )

    async def _complete(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        from app.modules.agent_surfaces.services.onboarding_workspace import (
            WorkspaceChoiceAsked,
            complete_onboarding_workspace,
        )

        try:
            waiting_on_an_admin = await complete_onboarding_workspace(
                self._uows, transport, state
            )
        except WorkspaceChoiceAsked as parked:
            # The one refusal that has already asked its own next question: the
            # row is parked on AWAITING_POD and the offer is in the message, so
            # sending it is the whole job. Ahead of the arm below, which would
            # end the signup and throw the question away in the act of asking
            # it.
            await self._reply(
                transport,
                destination,
                parked.message,
                step=OnboardingStep.AWAITING_POD,
            )
            return
        except ChallengeRejected as error:
            await self._refused(transport, state, destination, error)
            return
        await self._reply(
            transport,
            destination,
            "Your account is ready. Ask your team admin to add you to this "
            "Lemma organization."
            if waiting_on_an_admin
            else ready_message(),
        )

    async def _handoff(
        self, transport: OnboardingTransport, state: PendingState
    ) -> OnboardingIngressResult:
        event = transport.event
        destination = await private_onboarding_destination(
            event, credentials=transport.credentials
        )
        if state.expires_at <= datetime.now(timezone.utc):
            return await self._expire(transport, state, destination)
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
        # Retry private delivery before accepting any signup submission: the
        # shape every other advancing step now shares.
        await self._advance_if_delivered(
            state,
            {"destination": destination.model_dump(mode="json"), "step": step},
            lambda: self._send_initial_prompt(transport, destination, step),
        )
        return OnboardingIngressResult(True)

    async def _send_initial_prompt(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        step: str,
    ) -> None:
        await self._reply(transport, destination, initial_prompt_for(step), step=step)

    async def _contact(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        if (
            event.metadata.get("contact_shared_by_sender") is not True
            or not event.sender_phone
        ):
            await self._reply(
                transport,
                destination,
                "Use Share my contact. Typed numbers and other people's contacts cannot verify your phone.",
                step=OnboardingStep.AWAITING_PHONE,
            )
            return OnboardingIngressResult(True)
        phone = normalize_mobile_e164("+" + event.sender_phone.lstrip("+"))
        async with self._uows() as uow:
            matches = await live_user_ids_by_mobile_numbers(
                uow, [phone, phone.lstrip("+")], verified=True
            )
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.verified_phone = phone
            row.step = OnboardingStep.AWAITING_EMAIL
            if len(matches) == 1:
                row.user_id = matches[0]
                row.step = OnboardingStep.VERIFIED
        if len(matches) == 1:
            state = await self._require_state(transport.binding_key)
            assert state is not None
            await self._complete(transport, state, destination)
            return OnboardingIngressResult(True)
        await self._reply(
            transport,
            destination,
            "What's your email address? I'll send a code to verify it.",
            step=OnboardingStep.AWAITING_EMAIL,
        )
        return OnboardingIngressResult(True)

    async def _email(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        text = event.message_text.strip()
        email = parse_email_reply(text)
        if email is None:
            await self._reply(
                transport,
                destination,
                "Send one email address so I can send your verification code.",
                step=OnboardingStep.AWAITING_EMAIL,
            )
            return OnboardingIngressResult(True)
        receipt = await self._challenge_service(event.platform.value).start_challenge(
            email=email,
            binding=state.binding_key,
            purpose="chat_onboarding",
            sender_key=state.binding_key,
        )
        # Rolling back to AWAITING_EMAIL costs the retry a second code email,
        # and that is the cheap side of the trade: left on AWAITING_CODE with
        # no prompt ever delivered, the address they send again is read as a
        # wrong code and answered as one.
        await self._advance_if_delivered(
            state,
            {"challenge_id": receipt.id, "step": OnboardingStep.AWAITING_CODE},
            lambda: self._reply(
                transport,
                destination,
                "Check your email and send the six-digit code here. You can also change email or cancel.",
                step=OnboardingStep.AWAITING_CODE,
            ),
        )
        return OnboardingIngressResult(True)

    async def _code(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        challenges = self._challenge_service(event.platform.value)
        text = event.message_text.strip()
        if text.lower() == "resend":
            assert state.challenge_id is not None
            receipt = await challenges.resend_challenge(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
                sender_key=state.binding_key,
            )
            # Safe to put back: `resend_challenge` accepts an already-revoked
            # challenge, so the retry that re-runs this reaches the same place.
            await self._advance_if_delivered(
                state,
                {"challenge_id": receipt.id},
                lambda: self._reply(
                    transport,
                    destination,
                    "A new code is on its way. The previous code no longer works.",
                    step=OnboardingStep.AWAITING_CODE,
                ),
            )
            return OnboardingIngressResult(True)
        if text.lower() == "change email":
            assert state.challenge_id is not None
            await challenges.cancel_challenge(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
            # Not guarded, and deliberately. The challenge is already
            # cancelled, so putting the row back on AWAITING_CODE would point
            # it at a dead code -- and the advance needs no prompt to be
            # coherent: on AWAITING_EMAIL the next message is read as an
            # address, and anything that is not one is answered by `_email`
            # with the very instruction this reply carries.
            async with self._uows() as uow:
                row = await uow.session.get(PendingChatOnboarding, state.id)
                assert row is not None
                row.step = OnboardingStep.AWAITING_EMAIL
                row.challenge_id = None
            await self._reply(
                transport,
                destination,
                "Send the email address you want to use.",
                step=OnboardingStep.AWAITING_EMAIL,
            )
            return OnboardingIngressResult(True)
        assert state.challenge_id is not None
        await challenges.verify_challenge(
            challenge_id=state.challenge_id,
            binding=state.binding_key,
            purpose="chat_onboarding",
            submitted_code=text,
        )
        user_id = await complete_chat_account(
            self._uows,
            challenge_id=state.challenge_id,
            binding=state.binding_key,
        )
        # Also unguarded: `complete_chat_account` has already created or
        # resolved the account, so VERIFIED is a fact about the world and not a
        # question waiting on an answer. `_complete` below both provisions and
        # confirms, and its own reply failing leaves the row READY -- which the
        # next message resolves through `recognize_sender`, not through here.
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.user_id = user_id
            row.step = OnboardingStep.VERIFIED
        state = await self._require_state(transport.binding_key)
        assert state is not None
        await self._complete(transport, state, destination)
        return OnboardingIngressResult(True)
