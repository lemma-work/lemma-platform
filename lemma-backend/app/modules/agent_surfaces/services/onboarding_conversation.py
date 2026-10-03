"""What every step of a chat signup shares: its collaborators and how it talks.

`ChatOnboardingCoordinator` is assembled from this and two halves of the
conversation -- `onboarding_answers` (email, code, contact: proving who they
are) and `onboarding_settle` (where they will talk). Split because the
coordinator had reached the architecture ratchet's per-file ceiling, and the
fixes the flow needed next all added lines; a base class rather than free
functions because every step reads the same five collaborators and calls the
same two ways of replying, and threading them through each call would be most
of each call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingIngressResult,
    PendingState,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.infrastructure.adapters.redis_event_dedup_store import (
    get_surface_event_dedup_store,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.services.onboarding_outcomes import (
    OnboardingOutcomes,
)
from app.modules.agent_surfaces.services.onboarding_replies import say_privately
from app.modules.agent_surfaces.services.onboarding_row_writes import (
    advance_if_delivered,
)
from app.modules.agent_surfaces.services.onboarding_sender import (
    read_state,
    require_state,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.identity.contracts.onboarding import (
    ChallengeRejected,
    EmailChallengeService,
    email_challenge_service,
)


class OnboardingConversation:
    """The collaborators and replies every signup step uses."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        challenges: EmailChallengeService | None = None,
        event_dedup_store: SurfaceEventDedupStorePort | None = None,
        email_deliverable: Callable[[], bool] | None = None,
    ) -> None:
        self._uows = uow_factory
        self._adapters = SurfacePlatformAdapterRegistry()
        self._challenges = challenges
        self._event_dedup_store = event_dedup_store or get_surface_event_dedup_store()
        self._email_deliverable = email_deliverable

    def _can_email(self) -> bool:
        """Whether a code sent now would reach anybody's inbox.

        An injected challenge service brings its own delivery; otherwise it is
        the installation's mail, where a filesystem spool counts as none
        because nobody in a chat can read it.
        """
        from app.core.email.email_sender import email_delivery_state

        if self._email_deliverable is not None:
            return self._email_deliverable()
        return self._challenges is not None or email_delivery_state() == "sending"

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

    async def _advance_if_delivered(
        self,
        state: PendingState,
        advanced: dict[str, object],
        send: Callable[[], Awaitable[None]],
    ) -> None:
        """See `onboarding_row_writes.advance_if_delivered`."""
        await advance_if_delivered(self._uows, state, advanced, send)

    async def _refused(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
        error: ChallengeRejected,
    ) -> None:
        await self._outcomes.refused(transport, state, destination, error)

    async def _no_email(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> OnboardingIngressResult:
        await self._outcomes.email_unavailable(transport, state, destination)
        return OnboardingIngressResult(True)

    async def _settle(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        """Decide where a proven person talks; `onboarding_settle` implements it.

        Declared here because the answers half reaches it -- a verified code or
        a recognised contact ends in it -- and must not import the other half.
        """
        raise NotImplementedError
