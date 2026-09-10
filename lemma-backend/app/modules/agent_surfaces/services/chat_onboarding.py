"""Private, resumable signup before surface routing and conversation ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID
from datetime import datetime, timedelta, timezone

from pydantic import JsonValue
from sqlalchemy import select

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_context import AgentSurfaceContext
from app.modules.agent_surfaces.domain.ingress_request import SurfaceIngressRequest
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
    PersonalDMRoute,
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.onboarding_private_delivery import (
    private_onboarding_destination,
)
from app.modules.agent_surfaces.services.onboarding_inputs import native_prompt_metadata
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
    resolve_onboarding_transport,
)
from app.modules.agent_surfaces.services.personal_dm_routes import (
    prepare_personal_dm_context,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    RateLimitExceeded,
    EmailChallengeService,
    PENDING_TTL_SECONDS,
    active_chat_user,
    complete_chat_account,
    email_challenge_service,
    hold_chat_onboarding,
    parse_email_reply,
)
from app.modules.identity.contracts.surfaces import (
    live_user_ids_by_mobile_numbers,
)


from app.modules.agent_surfaces.domain.onboarding_state import PendingState


@dataclass(frozen=True, slots=True)
class OnboardingIngressResult:
    handled: bool
    context: AgentSurfaceContext | None = None


def _original_request(event: ParsedInboundSurfaceEvent) -> dict[str, JsonValue]:
    # Surrounding channel history and arbitrary platform payloads never enter
    # the personal assistant's conversation. Attachments retain provider IDs.
    return event.model_copy(
        update={
            "metadata": {"attachments": event.metadata.get("attachments", [])},
            "raw_payload": {},
        }
    ).model_dump(mode="json")


class ChatOnboardingCoordinator:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        challenges: EmailChallengeService | None = None,
    ) -> None:
        self._uows = uow_factory
        self._adapters = SurfacePlatformAdapterRegistry()
        self._challenges = challenges

    def _challenge_service(self, platform: str) -> EmailChallengeService:
        return self._challenges or email_challenge_service(platform)

    async def _state(self, binding_key: str) -> PendingState | None:
        async with self._uows() as uow:
            row = await uow.session.scalar(
                select(PendingChatOnboarding).where(
                    PendingChatOnboarding.binding_key == binding_key
                )
            )
            return PendingState.model_validate(row) if row is not None else None

    async def _require_state(self, binding_key: str) -> PendingState:
        state = await self._state(binding_key)
        if state is None:
            raise ValueError("Pending onboarding disappeared")
        return state

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
        if state is None or state.handed_off_at is not None:
            started = await self._new_sender(transport)
            if isinstance(started, OnboardingIngressResult):
                return started
            state = started
        if state.step == "handoff":
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
        if state.step == "awaiting_phone":
            return await self._contact(transport, state, destination)
        try:
            if state.step == "awaiting_email":
                return await self._email(transport, state, destination)
            if state.step == "awaiting_code":
                return await self._code(transport, state, destination)
            if state.user_id is not None:
                await self._complete(transport, state, destination)
        except ChallengeRejected as error:
            await self._reply(transport, destination, error.message)
        except RateLimitExceeded:
            await self._reply(
                transport, destination, "Too many code requests. Try again later."
            )
        return OnboardingIngressResult(True)

    async def _cancel(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        event = transport.event
        if state.challenge_id is not None:
            await self._challenge_service(event.platform.value).cancel(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.original_event = None
            row.step = "cancelled"
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

    async def _verified_sender(
        self, binding_key: str
    ) -> tuple[UUID | None, bool, UUID | None]:
        async with self._uows() as uow:
            identity = await uow.session.scalar(
                select(VerifiedSurfaceIdentity).where(
                    VerifiedSurfaceIdentity.binding_key == binding_key
                )
            )
            verified_user_id = (
                identity.user_id
                if identity is not None and identity.revoked_at is None
                else None
            )
            previously_revoked = (
                identity is not None and identity.revoked_at is not None
            )
            if verified_user_id is not None:
                assert identity is not None
                verified_user = await active_chat_user(uow, verified_user_id)
                if verified_user is None or (
                    identity.verified_phone is not None
                    and (
                        identity.verified_phone != verified_user.mobile_number
                        or verified_user.mobile_verified_at is None
                    )
                ):
                    verified_user_id = None
                    previously_revoked = True
            route_id = await uow.session.scalar(
                select(PersonalDMRoute.id).where(
                    PersonalDMRoute.binding_key == binding_key
                )
            )
        return verified_user_id, previously_revoked, route_id

    async def _create_pending(
        self, transport: OnboardingTransport, event: ParsedInboundSurfaceEvent
    ) -> PendingState:
        async with self._uows() as uow:
            row = await uow.session.scalar(
                select(PendingChatOnboarding).where(
                    PendingChatOnboarding.binding_key == transport.binding_key
                )
            )
            if row is not None:
                await uow.session.delete(row)
                await uow.session.flush()
            row = PendingChatOnboarding(
                binding_key=transport.binding_key,
                platform=event.platform.value,
                step="handoff",
                destination={},
                original_event=_original_request(event),
                installation_surface_id=transport.surface.id
                if transport.surface
                else None,
                expires_at=datetime.now(timezone.utc)
                + timedelta(
                    seconds=min(
                        PENDING_TTL_SECONDS,
                        surface_settings.surface_onboarding_ttl_seconds,
                    )
                ),
                verified_phone=normalize_mobile_e164(
                    "+"
                    + str(event.sender_phone or event.sender_external_user_id).lstrip(
                        "+"
                    )
                )
                if event.platform == SurfacePlatform.WHATSAPP
                else None,
            )
            uow.session.add(row)
        state = await self._require_state(transport.binding_key)
        assert state is not None
        return state

    async def _new_sender(
        self, transport: OnboardingTransport
    ) -> PendingState | OnboardingIngressResult:
        event = transport.event
        verified_user_id, previously_revoked, route_id = await self._verified_sender(
            transport.binding_key
        )
        if verified_user_id is not None and route_id is not None and event.is_dm:
            async with self._uows() as uow:
                context = await prepare_personal_dm_context(
                    uow, route_id=route_id, event=event
                )
            return OnboardingIngressResult(True, context)
        if verified_user_id is not None:
            return OnboardingIngressResult(False)
        if not previously_revoked:
            adapter = self._adapters.get(event.platform)
            assert adapter is not None
            profile = await adapter.fetch_sender_profile(
                credentials=transport.credentials, event=event
            )
            async with self._uows() as uow:
                resolved = await SurfaceIdentityResolutionService(
                    uow, ExternalSurfaceUserRepository(uow)
                ).resolve(
                    event=event, sender_profile=profile, require_proven_identity=True
                )
            if resolved.internal_user_id is not None:
                return OnboardingIngressResult(False)
            if profile is not None:
                event = event.model_copy(
                    update={
                        "sender_email": profile.email,
                        "sender_display_name": profile.display_name
                        or event.sender_display_name,
                    }
                )
        return await self._create_pending(transport, event)

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
            "awaiting_phone"
            if event.platform == SurfacePlatform.TELEGRAM
            else "awaiting_email"
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
                    row.step = "handoff"
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
            if step == "awaiting_phone"
            else "What's your email address? I'll send a code to verify it.",
            contact=step == "awaiting_phone",
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
            row.step = "awaiting_email"
            if len(matches) == 1:
                row.user_id = matches[0]
                row.step = "verified"
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
        receipt = await self._challenge_service(event.platform.value).start(
            email=email,
            binding=state.binding_key,
            purpose="chat_onboarding",
            sender_key=state.binding_key,
        )
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.challenge_id = receipt.id
            row.step = "awaiting_code"
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
        text = event.message_text.strip()
        if text.lower() == "resend":
            assert state.challenge_id is not None
            receipt = await self._challenge_service(event.platform.value).resend(
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
            await self._challenge_service(event.platform.value).cancel(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
            async with self._uows() as uow:
                row = await uow.session.get(PendingChatOnboarding, state.id)
                assert row is not None
                row.step = "awaiting_email"
                row.challenge_id = None
            await self._reply(
                transport,
                destination,
                "Send the email address you want to use.",
            )
            return OnboardingIngressResult(True)
        assert state.challenge_id is not None
        await self._challenge_service(event.platform.value).verify(
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
            row.step = "verified"
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
                await self._challenge_service(event.platform.value).cancel(
                    challenge_id=state.challenge_id,
                    binding=state.binding_key,
                    purpose="chat_onboarding",
                )
            except ChallengeRejected:
                pass
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.original_event = None
            row.step = "expired"
            row.handed_off_at = datetime.now(timezone.utc)
        await self._reply(
            transport,
            destination,
            "Setup expired. Send a fresh request to start again.",
        )
        return OnboardingIngressResult(True)
