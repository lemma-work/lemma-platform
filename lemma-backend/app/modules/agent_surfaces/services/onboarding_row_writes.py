"""Advancing a pending signup's row only as far as the person was told.

Split from `ChatOnboardingCoordinator` because it is a rule about the row, not
a step of the conversation: every step that writes before it sends shares it,
and the coordinator was at the architecture ratchet's per-file ceiling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.domain.onboarding_state import PendingState
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    PendingChatOnboarding,
)


async def _write(
    uows: UnitOfWorkFactory, state_id: UUID, values: dict[str, object]
) -> None:
    async with uows() as uow:
        row = await uow.session.get(PendingChatOnboarding, state_id)
        assert row is not None
        for column, value in values.items():
            setattr(row, column, value)


async def advance_if_delivered(
    uows: UnitOfWorkFactory,
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
    await _write(uows, state.id, advanced)
    delivered = False
    try:
        await send()
        delivered = True
    finally:
        if not delivered:
            await _write(uows, state.id, before)
