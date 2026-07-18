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
    """Transcribe a user's voice message or audio file to text (speech-to-text).

    USE WHEN the user sends a voice note / audio file and you need its words.
    The file_path may point at the pod datastore (e.g. an auto-ingested voice
    note at /me/telegram/voice.ogg) or the workspace sandbox. Common formats
    (OGG/Opus, MP3, M4A/AAC, WAV, FLAC, WebM) are supported directly.

    Returns the transcript plus optional detected_language/duration. The
    transcript is for YOUR understanding — treat it as if the user had typed
    those words and act on the request directly. Do NOT paste, echo, or rewrite
    the transcript back to the user as a message ("You said: ...").
    """
    try:
        return await listen_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent.speech.listen_failed", exc_info=True)
        return ListenResponse(success=False, error=str(exc))


async def say(ctx: RunContext[BaseAgentContext], request: SayRequest) -> SayResponse:
    """Speak a reply: synthesize audio from text and deliver it as a voice note.

    USE WHEN the user wants a spoken reply — the default reply modality is text,
    so only call this when voice is genuinely wanted (e.g. they sent a voice note
    and want one back). On a chat surface this delivers a NATIVE voice note
    automatically (Telegram voice bubble, WhatsApp audio, Slack audio player) and
    also saves the audio to the pod datastore; on the web app it appears as an
    audio player. The user receives and can play this audio — it IS your reply.

    Do NOT call display_resource afterward — say already delivers. Do NOT also
    write the same words as a text message: the spoken audio is the reply, and
    restating it as text duplicates it. Add a separate text line only if it says
    something different (a caption, a link). Returns the pod file path of the
    generated audio.
    """
    try:
        return await say_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent.speech.say_failed", exc_info=True)
        return SayResponse(success=False, error=str(exc))


speech_toolset = FunctionToolset[BaseAgentContext](tools=[listen, say])
