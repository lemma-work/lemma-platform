"""Private, resumable signup before surface routing and conversation ingestion."""

from __future__ import annotations

from datetime import datetime, timezone


from app.core.helpers.identifiers import normalize_mobile_e164
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
from app.modules.agent_surfaces.services.onboarding_inputs import native_prompt_metadata
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
            return OnboardingIngressResult(True)
        if state.expires_at <= datetime.now(timezone.utc) and state.user_id is None:
            return await self._expire(transport, state, destination)
        text = event.message_text.strip()
        if text.lower() == "cancel":
            return await self._cancel(transport, state, destination)
        if state.step == OnboardingStep.AWAITING_PHONE:
            return await self._contact(transport, state, destination)
        return await self._step(transport, state, destination)

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
            await self._reply(transport, destination, error.message)
        except RateLimitExceeded:
            await self._reply(
                transport, destination, "Too many code requests. Try again later."
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
                transport, destination, offer_text(state.offered_pods or [])
            )
            return OnboardingIngressResult(True)
        refusal = await attach_chosen_workspace(self._uows, transport, state, choice)
        if refusal is not None:
            await self._reply(transport, destination, refusal)
        return OnboardingIngressResult(True)

    async def _cancel(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        if state.challenge_id is not None:
            await self._challenge_service(event.platform.value).cancel_challenge(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.original_event = None
            row.step = OnboardingStep.CANCELLED
            row.handed_off_at = datetime.now(timezone.utc)
        await self._reply(transport, destination, "Setup cancelled.")
        return OnboardingIngressResult(True)

    async def _reply(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        message: str,
        *,
        contact: bool = False,
    ) -> None:
        if not destination.is_dm:
            raise ValueError("Onboarding replies require a private destination")
        adapter = self._adapters.get(destination.platform)
        assert adapter is not None
        metadata = await native_prompt_metadata(
            self._uows, binding_key=transport.binding_key, platform=destination.platform
        )
        metadata["private_onboarding"] = True
        if destination.platform == SurfacePlatform.TELEGRAM:
            metadata["reply_markup"] = (
                {
                    "keyboard": [
                        [{"text": "Share my contact", "request_contact": True}]
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                }
                if contact
                else {
                    "keyboard": [
                        [{"text": "Resend"}, {"text": "Change email"}],
                        [{"text": "Cancel"}],
                    ],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                }
            )
        await adapter.send_message(
            credentials=transport.credentials,
            event=destination,
            message=message,
            metadata=metadata,
        )

    async def _complete(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        from app.modules.agent_surfaces.services.onboarding_workspace import (
            complete_onboarding_workspace,
        )

        if await complete_onboarding_workspace(self._uows, transport, state):
            await self._reply(
                transport,
                destination,
                "Your account is ready. Ask your team admin to add you to this Lemma organization.",
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
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.destination = destination.model_dump(mode="json")
            row.step = step
        delivered = False
        try:
            await self._send_initial_prompt(transport, destination, step)
            delivered = True
        finally:
            if not delivered:
                # Retry private delivery before accepting any signup submission.
                async with self._uows() as uow:
                    row = await uow.session.get(PendingChatOnboarding, state.id)
                    assert row is not None
                    row.step = OnboardingStep.HANDOFF
        return OnboardingIngressResult(True)

    async def _send_initial_prompt(
        self,
        transport: OnboardingTransport,
        destination: ParsedInboundSurfaceEvent,
        step: str,
    ) -> None:
        await self._reply(
            transport,
            destination,
            "Share your own contact using the button below."
            if step == OnboardingStep.AWAITING_PHONE
            else "What's your email address? I'll send a code to verify it.",
            contact=step == OnboardingStep.AWAITING_PHONE,
        )

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
                contact=True,
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
            )
            return OnboardingIngressResult(True)
        receipt = await self._challenge_service(event.platform.value).start_challenge(
            email=email,
            binding=state.binding_key,
            purpose="chat_onboarding",
            sender_key=state.binding_key,
        )
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.challenge_id = receipt.id
            row.step = OnboardingStep.AWAITING_CODE
        await self._reply(
            transport,
            destination,
            "Check your email and send the six-digit code here. You can also change email or cancel.",
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
            async with self._uows() as uow:
                row = await uow.session.get(PendingChatOnboarding, state.id)
                assert row is not None
                row.challenge_id = receipt.id
            await self._reply(
                transport,
                destination,
                "A new code is on its way. The previous code no longer works.",
            )
            return OnboardingIngressResult(True)
        if text.lower() == "change email":
            assert state.challenge_id is not None
            await challenges.cancel_challenge(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
            async with self._uows() as uow:
                row = await uow.session.get(PendingChatOnboarding, state.id)
                assert row is not None
                row.step = OnboardingStep.AWAITING_EMAIL
                row.challenge_id = None
            await self._reply(
                transport,
                destination,
                "Send the email address you want to use.",
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
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.user_id = user_id
            row.step = OnboardingStep.VERIFIED
        state = await self._require_state(transport.binding_key)
        assert state is not None
        await self._complete(transport, state, destination)
        return OnboardingIngressResult(True)

    async def _expire(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        if state.challenge_id is not None:
            try:
                await self._challenge_service(event.platform.value).cancel_challenge(
                    challenge_id=state.challenge_id,
                    binding=state.binding_key,
                    purpose="chat_onboarding",
                )
            except ChallengeRejected:
                # Expiry is the caller here, and the challenge being already
                # revoked, already used or itself expired is the ordinary way
                # to arrive: the point of the call is that no live code is left
                # behind, and all three refusals mean there is none.
                pass
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.original_event = None
            row.step = OnboardingStep.EXPIRED
            row.handed_off_at = datetime.now(timezone.utc)
        await self._reply(
            transport,
            destination,
            "Setup expired. Send a fresh request to start again.",
        )
        return OnboardingIngressResult(True)
