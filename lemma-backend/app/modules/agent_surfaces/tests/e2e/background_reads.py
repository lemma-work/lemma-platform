"""What a run's background reads, for a test holding the message it rides on.

A run's message keeps which lines of the group it was handed, never what they
said (``channels.contracts.backgrounds``); this reads them from the timeline
the way the run's prompt does. Not a test module; pytest does not collect it.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundRefs,
    ShownBackground,
    read_backgrounds,
)


async def background_read(metadata: Mapping[str, object] | None) -> ShownBackground:
    """The background of the message with this metadata, as its run reads it now."""
    refs = (metadata or {}).get(BACKGROUND_KEY)
    if refs is None:
        return ShownBackground()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        [shown] = await read_backgrounds(uow, [BackgroundRefs.model_validate(refs)])
    return shown
