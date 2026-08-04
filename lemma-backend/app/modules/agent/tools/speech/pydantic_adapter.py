from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.speech.models import (
    ListenRequest,
    ListenResponse,
    SayRequest,
    SayResponse,
)
from app.modules.agent.tools.speech.speech import listen_internal, say_internal

logger = get_logger(__name__)


async def listen(
    ctx: RunContext[BaseAgentContext], request: ListenRequest
) -> ListenResponse:
    """Transcribe a voice message or audio file to text.

    `file_path` may be a pod path (e.g. `/me/telegram/voice.ogg`) or a workspace
    path; common formats are supported directly.

    The transcript is for YOUR understanding — act on it as if the user had typed
    it. Never echo it back ("You said: ...").
    """
    try:
        return await listen_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent.speech.listen_failed", exc_info=True)
        return ListenResponse(success=False, error=str(exc))


async def say(ctx: RunContext[BaseAgentContext], request: SayRequest) -> SayResponse:
    """Speak a reply: synthesize audio and deliver it as a voice note.

    Text is the default modality, so call this only when voice is genuinely
    wanted. Delivery is automatic — a native voice note on chat surfaces, an
    audio player on web.

    The audio IS your reply. Do not call `display_resource` afterward, and do not
    restate the same words as text; add a text line only if it says something
    different.
    """
    try:
        return await say_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent.speech.say_failed", exc_info=True)
        return SayResponse(success=False, error=str(exc))


speech_toolset = FunctionToolset[BaseAgentContext](tools=[listen, say])
