"""The one tool a call's coordinator gets: the voice.

What a call shows is not a tool of its own. Anything any run in the call shows
with `display_resource` is on the call's canvas, so agents show things the way
they always do, and the same item reads the same in chat, on a surface and on
a call.

The relay is recorded, not delivered, here. A tool call is already a persisted
message on the conversation's stream; the call session on the API listens to
that stream and does the delivering, so the run needs no route to the replica
holding the caller's socket.
"""

from __future__ import annotations

from app.modules.agent.infrastructure.tools.call.models import (
    RelayToVoiceRequest,
    RelayToVoiceResponse,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.tools import ToolGroup, tool


async def relay_to_voice(
    ctx: ToolContext, request: RelayToVoiceRequest
) -> RelayToVoiceResponse:
    """Give the call's voice something to know or to say.

    Write notes, not a script: the voice says it in its own words. `context`
    is known but not said now; `when_idle` is said at the next pause; `now` is
    said as soon as the person stops talking, for failures and questions only.
    """
    del ctx
    # Recorded, not delivered: the call's voice may still drop it, so the
    # coordinator is not told it was said.
    spoken = "to be said" if request.urgency != "context" else "for the voice to know"
    return RelayToVoiceResponse(success=True, message=f"Relayed {spoken}.")


SPECS = (tool(relay_to_voice, ToolGroup.CALL),)
