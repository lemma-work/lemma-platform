"""The proving half of a chat signup: a shared contact, an email, a code.

Each of these reads the message as an answer to the question the row is on,
which is exactly why they only run on those steps. What happens once the person
is proven is `onboarding_settle`'s; these end by handing over to it.
"""

from __future__ import annotations

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_contact import contact_owner
from app.modules.agent_surfaces.services.onboarding_conversation import (
    OnboardingConversation,
)
from app.modules.agent_surfaces.services.onboarding_inputs import command_of
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.identity.contracts.onboarding import (
    ACCOUNT_RETRY,
    PERMANENT_ACCOUNT_REFUSALS,
    ChallengeRejected,
    complete_chat_account,
    parse_email_reply,
)

logger = get_logger(__name__)

#: The code prompt, which is also the only place the person learns the three
#: words this step answers to. On WhatsApp there are no buttons for them, so a
#: prompt that did not name them left "resend" undiscoverable.
CODE_PROMPT = (
    "Check your email and send the six-digit code here. Reply `resend` for a "
    "new code, `change email` to use a different address, or `cancel` to stop."
)


class OnboardingAnswers(OnboardingConversation):
    """Contact, email and code: the steps that establish who this is."""

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
        user_id = await contact_owner(self._uows, phone)
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.verified_phone = phone
            row.step = OnboardingStep.AWAITING_EMAIL
            if user_id is not None:
                row.user_id = user_id
                row.step = OnboardingStep.VERIFIED
        state = await self._require_state(transport.binding_key)
        if user_id is not None:
            await self._settle(transport, state, destination)
            return OnboardingIngressResult(True)
        if not self._can_email():
            return await self._no_email(transport, state, destination)
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
        email = parse_email_reply(event.message_text.strip())
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
                transport, destination, CODE_PROMPT, step=OnboardingStep.AWAITING_CODE
            ),
        )
        return OnboardingIngressResult(True)

    async def _resend(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        assert state.challenge_id is not None
        receipt = await self._challenge_service(
            transport.event.platform.value
        ).resend_challenge(
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

    async def _change_email(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        assert state.challenge_id is not None
        await self._challenge_service(transport.event.platform.value).cancel_challenge(
            challenge_id=state.challenge_id,
            binding=state.binding_key,
            purpose="chat_onboarding",
        )
        # Not guarded, and deliberately. The challenge is already cancelled, so
        # putting the row back on AWAITING_CODE would point it at a dead code --
        # and the advance needs no prompt to be coherent: on AWAITING_EMAIL the
        # next message is read as an address, and anything that is not one is
        # answered by `_email` with the very instruction this reply carries.
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

    async def _code(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        command = command_of(transport.event.message_text)
        if command == "resend":
            return await self._resend(transport, state, destination)
        if command == "change email":
            return await self._change_email(transport, state, destination)
        assert state.challenge_id is not None
        await self._challenge_service(transport.event.platform.value).verify_challenge(
            challenge_id=state.challenge_id,
            binding=state.binding_key,
            purpose="chat_onboarding",
            submitted_code=transport.event.message_text.strip(),
        )
        try:
            user_id = await complete_chat_account(
                self._uows,
                challenge_id=state.challenge_id,
                binding=state.binding_key,
            )
        except ChallengeRejected as error:
            return await self._account_refused(transport, state, destination, error)
        # Also unguarded: `complete_chat_account` has already created or
        # resolved the account, so VERIFIED is a fact about the world and not a
        # question waiting on an answer.
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.user_id = user_id
            row.step = OnboardingStep.VERIFIED
        state = await self._require_state(transport.binding_key)
        await self._settle(transport, state, destination)
        return OnboardingIngressResult(True)

    async def _account_refused(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
        error: ChallengeRejected,
    ) -> OnboardingIngressResult:
        """The code was right and the account still could not be finished.

        Two kinds, told apart by code. A permanent one -- an account that
        cannot sign in, a signup the deployment does not admit, identities that
        need support to untangle -- ends the signup, because staying on
        AWAITING_CODE read every later message as a code against a challenge
        already spent, and answered with the same refusal forever. A transient
        one stays, and says that sending anything retries: the challenge is
        verified, so the next message replays it straight into completion.
        """
        if error.code in PERMANENT_ACCOUNT_REFUSALS:
            await self._refused(transport, state, destination, error)
            return OnboardingIngressResult(True)
        if error.code != ACCOUNT_RETRY:
            raise error
        logger.info(
            "agent_surfaces.chat_onboarding.account_retry.observed",
            platform=state.platform,
            reason=error.message,
        )
        await self._reply(
            transport,
            destination,
            "Your code was right, but I couldn't finish setting up your account "
            "just now. Send any message to try again.",
            step=OnboardingStep.AWAITING_CODE,
        )
        return OnboardingIngressResult(True)
