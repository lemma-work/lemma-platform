"""The three ways a signup ends without an account at the other side.

Cancelled because they asked, expired because nobody came back, refused because
it cannot be finished. Separated from `ChatOnboardingCoordinator` because they
are one thing said three ways, and saying it three times in the middle of the
state machine is what let `_refused` go missing in the first place -- a refusal
that replied and changed nothing, so the next message re-entered the same branch
and was refused in the same words, with no exit and nothing logged.

All three do exactly the same thing to the row: drop the held message, set a
terminal step, and stamp `handed_off_at` so the next message goes through
`recognize_sender` and starts afresh. That is `_end_with`, and having it in one
place is the point -- the bug was one of the three not doing it.

An object with a constructor rather than free functions: three collaborators,
all read by every method.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.onboarding_state import (
    OnboardingStep,
    PendingState,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)
from app.modules.agent_surfaces.services.onboarding_replies import say_privately
from app.modules.agent_surfaces.services.onboarding_transport import (
    OnboardingTransport,
)
from app.modules.identity.contracts.onboarding import ChallengeRejected

logger = get_logger(__name__)


class OnboardingOutcomes:
    """Ending a signup, and telling the person which ending it was."""

    def __init__(
        self,
        *,
        uows: UnitOfWorkFactory,
        adapters: SurfacePlatformAdapterRegistry,
        challenge_service,
    ) -> None:
        self._uows = uows
        self._adapters = adapters
        self._challenge_service = challenge_service

    async def _end_with(self, state: PendingState, step: str) -> None:
        """Close the row so the next message starts a fresh signup.

        `handed_off_at` is the exit: it sends the next message through
        `recognize_sender` rather than back into the step that just ended. A
        terminal step without it is a state whose only property is that it
        cannot be left.
        """
        async with self._uows() as uow:
            row = await uow.session.get(PendingChatOnboarding, state.id)
            assert row is not None
            row.original_event = None
            row.step = step
            row.handed_off_at = datetime.now(timezone.utc)

    async def _revoke_code(self, state: PendingState, platform: str) -> None:
        if state.challenge_id is None:
            return
        try:
            await self._challenge_service(platform).cancel_challenge(
                challenge_id=state.challenge_id,
                binding=state.binding_key,
                purpose="chat_onboarding",
            )
        except ChallengeRejected:
            # Already revoked, already used, or itself expired. The point of
            # the call is that no live code is left behind, and all three
            # refusals mean there is none.
            pass

    async def cancelled(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        await self._revoke_code(state, transport.event.platform.value)
        await self._end_with(state, OnboardingStep.CANCELLED)
        await say_privately(
            self._adapters, self._uows, transport, destination, "Setup cancelled."
        )

    async def expired(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
    ) -> None:
        await self._revoke_code(state, transport.event.platform.value)
        await self._end_with(state, OnboardingStep.EXPIRED)
        await say_privately(
            self._adapters,
            self._uows,
            transport,
            destination,
            "Setup expired. Send a fresh request to start again.",
        )

    async def refused(
        self,
        transport: OnboardingTransport,
        state: PendingState,
        destination: ParsedInboundSurfaceEvent,
        error: ChallengeRejected,
    ) -> None:
        """End a signup that cannot be finished, instead of refusing it forever.

        Reached only from the two places that provision a workspace, because
        only there is a refusal known to be permanent. Three arrive here and all
        three are permanent for this row: a live platform identity bound to
        another account, a phone already proven by another account -- which
        every WhatsApp signup on a re-used number hits -- and an account that
        cannot chat.

        Ending it is a real second chance rather than a dead end, because the
        way out of two of the three is to finish as a different account.

        `error.message` rather than a generic line: the person has to know which
        of the three it was to have any idea what to do about it.
        """
        logger.error(
            "agent_surfaces.chat_onboarding.signup_refused.failed",
            platform=state.platform,
            step=state.step,
            reason=error.message,
            # LOG014 reads "not inside an `except`" as "no exception to
            # attach". This is called from one, and the traceback is the only
            # thing that says which of the three refusals it was and from
            # where -- before this nothing was logged here at all.
            exc_info=True,  # noqa: LOG014
        )
        await self._end_with(state, OnboardingStep.REFUSED)
        await say_privately(
            self._adapters,
            self._uows,
            transport,
            destination,
            f"{error.message}. This setup has stopped -- send a new message to "
            "start again.",
        )
