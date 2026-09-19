from __future__ import annotations

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.speech.models import (
    ListenRequest,
    ListenResponse,
    ListVoicesRequest,
    ListVoicesResponse,
    SayRequest,
    SayResponse,
    VoiceSummary,
)
from app.modules.agent.tools.speech.speech import listen_internal, say_internal

logger = get_logger(__name__)


async def listen(
    ctx: RunContext[BaseAgentContext], request: ListenRequest
) -> ListenResponse:
    """Transcribe an audio file that has NOT already been transcribed.

    A voice note the user sent you on a chat surface is transcribed before the
    message reaches you — their words are already in the message, so calling
    this on it just pays for the same transcript twice. Use it for audio you
    went and found: a recording in the datastore, a file someone shared as a
    file, something you downloaded.

    `file_path` may be a pod path (e.g. `/me/telegram/voice.ogg`) or a workspace
    path; common formats are supported directly.

    The transcript is for YOUR understanding — act on it as if the user had typed
    it. Never echo it back ("You said: ...").
    """
    try:
        return await listen_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agent.speech.listen_failed.degraded", exc_info=True)
        return ListenResponse(success=False, error=safe_error_text(exc))


async def say(ctx: RunContext[BaseAgentContext], request: SayRequest) -> SayResponse:
    """Speak a reply: synthesize audio and deliver it as a voice note.

    Text is the default modality, so call this only when voice is genuinely
    wanted. Delivery is automatic — a native voice note on chat surfaces, an
    audio player on web.

    Speaking anything other than English: pass `language` (the BCP-47 code the
    text is in) so the voice speaks that language instead of reading it aloud
    in English.

    The audio IS your reply. Do not call `display_resource` afterward, and do not
    restate the same words as text; add a text line only if it says something
    different.
    """
    try:
        return await say_internal(ctx.deps, request)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agent.speech.say_failed.degraded", exc_info=True)
        return SayResponse(success=False, error=safe_error_text(exc))


async def list_voices(
    ctx: RunContext[BaseAgentContext], request: ListVoicesRequest
) -> ListVoicesResponse:
    """What `say` can sound like: the voices this deployment can actually use.

    Read from the provider rather than a list kept here, so it is the whole
    range and cannot go stale. Filter by `language` when you know it; each
    voice carries an accent and use-case tags, which is usually what decides
    between two that speak the same language.

    A language with no voices is a real answer: say so rather than speaking it
    with a voice that does not.
    """
    del ctx
    from app.modules.agent.tools.speech.voice_catalogue import load_voices

    voices = await load_voices()
    if not voices:
        return ListVoicesResponse(
            success=False,
            error=(
                "The voice catalogue could not be read — no speech credentials, "
                "or the provider is unreachable. `say` still works and will "
                "pick a voice for the language."
            ),
        )
    matching = [v for v in voices if not request.language or v.speaks(request.language)]
    if not matching:
        return ListVoicesResponse(
            success=True,
            total=0,
            message=(
                f"No voice speaks '{request.language}'. Say so rather than "
                "reading that language aloud in another accent."
            ),
        )
    return ListVoicesResponse(
        success=True,
        total=len(matching),
        voices=[
            VoiceSummary(
                name=v.name,
                languages=list(v.languages),
                accent=v.accent or None,
                tags=list(v.tags),
            )
            for v in matching[: request.limit]
        ],
    )


speech_toolset = FunctionToolset[BaseAgentContext](tools=[listen, say, list_voices])
