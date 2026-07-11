"""Internal orchestration for the speech toolset (listen/say).

listen reads an audio file from either the pod datastore or the workspace
sandbox (via the dual-store bridge) and transcribes it. say synthesizes speech
and writes it to the pod datastore (the user-facing source of truth), returning
the pod path for the agent to deliver via display_resource(type=FILE).
"""

from __future__ import annotations

import posixpath
from uuid import uuid7

from app.core.log.log import get_logger
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.file_access import read_agent_file_bytes
from app.modules.agent.tools.pod.pod_data_access import pod_services
from app.modules.agent.tools.speech.models import (
    ListenRequest,
    ListenResponse,
    SayRequest,
    SayResponse,
)
from app.modules.agent.tools.speech.provider import get_speech_provider

logger = get_logger(__name__)

_MAX_AUDIO_BYTES = 50 * 1024 * 1024
_SUPPORTED_TTS_FORMATS = {"mp3", "wav", "ogg", "opus"}


async def listen_internal(
    deps: BaseAgentContext, request: ListenRequest
) -> ListenResponse:
    path = (request.file_path or "").strip()
    if not path:
        return ListenResponse(success=False, error="file_path is required.")
    try:
        content, mime = await read_agent_file_bytes(deps, path)
    except FileNotFoundError:
        return ListenResponse(success=False, error=f"File not found: {path}")
    except Exception as exc:
        logger.warning("speech.listen read failed path=%s error=%s", path, exc)
        return ListenResponse(success=False, error=f"Could not read file: {exc}")

    if not content:
        return ListenResponse(success=False, error="The audio file is empty.")
    if len(content) > _MAX_AUDIO_BYTES:
        return ListenResponse(
            success=False,
            error="Audio file is too large to transcribe; trim or segment it.",
        )

    try:
        provider = get_speech_provider()
        result = await provider.transcribe(
            content,
            mime=mime or "application/octet-stream",
            language=request.language,
        )
    except Exception as exc:
        logger.warning("speech.listen transcribe failed path=%s error=%s", path, exc)
        return ListenResponse(success=False, error=f"Transcription failed: {exc}")

    return ListenResponse(
        success=True,
        transcript=result.text,
        detected_language=result.detected_language,
        duration_seconds=result.duration_seconds,
        message="Transcribed audio.",
    )


def _resolve_output(
    request: SayRequest, default_format: str = "mp3"
) -> tuple[str, str, str]:
    """Return (directory_path, file_name, output_format) for the generated audio.

    With no explicit ``output_file_path`` the file is named with ``default_format``
    (the platform-native voice format), so the saved copy matches what's delivered.
    """
    fmt = default_format if default_format in _SUPPORTED_TTS_FORMATS else "mp3"
    raw = (request.output_file_path or "").strip()
    if not raw:
        return "/me/speech", f"{uuid7().hex}.{fmt}", fmt
    absolute = raw if raw.startswith("/") else f"/me/{raw}"
    directory = posixpath.dirname(absolute) or "/me/speech"
    name = posixpath.basename(absolute) or f"{uuid7().hex}.{fmt}"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in _SUPPORTED_TTS_FORMATS:
        name = f"{name}.{fmt}"
        ext = fmt
    return directory, name, ext


def _voice_note_format_for(platform: str | None) -> str:
    if not platform:
        return "mp3"
    from app.composition.agent_surface_runtime import voice_note_format

    return voice_note_format(platform)


async def _deliver_voice_note(deps: BaseAgentContext, path: str) -> bool:
    """Best-effort native voice-note delivery on a chat surface (skips email)."""
    platform = getattr(deps, "surface_platform", None)
    conversation_id = getattr(deps, "conversation_id", None)
    if not platform or not conversation_id:
        return False
    from app.composition.agent_surface_runtime import platform_supports_chat_delivery

    if not platform_supports_chat_delivery(platform):
        # Email composes one reply via the reply tool; the agent attaches the
        # audio there. Web/non-surface just gets the file path (audio player).
        return False
    try:
        from app.composition.agent_surface_runtime import deliver_voice_note

        return await deliver_voice_note(
            conversation_id=conversation_id, file_path=path
        )
    except Exception as exc:
        logger.warning("speech.say surface delivery failed error=%s", exc)
        return False


async def say_internal(deps: BaseAgentContext, request: SayRequest) -> SayResponse:
    text = (request.text or "").strip()
    if not text:
        return SayResponse(success=False, error="text is required.")

    default_format = _voice_note_format_for(getattr(deps, "surface_platform", None))
    directory, name, output_format = _resolve_output(request, default_format)
    try:
        provider = get_speech_provider()
        audio_bytes = await provider.synthesize(
            text, voice=request.voice, output_format=output_format
        )
    except Exception as exc:
        logger.warning("speech.say synthesize failed error=%s", exc)
        return SayResponse(success=False, error=f"Speech synthesis failed: {exc}")

    if not audio_bytes:
        return SayResponse(success=False, error="Speech synthesis returned no audio.")

    try:
        async with pod_services(deps) as services:
            entity = await services.file.create_file(
                pod_id=deps.pod_id,
                name=name,
                file_content=audio_bytes,
                ctx=services.ctx,
                directory_path=directory,
                search_enabled=False,
            )
    except Exception as exc:
        logger.warning("speech.say persist failed error=%s", exc)
        return SayResponse(success=False, error=f"Could not save audio: {exc}")

    delivered = await _deliver_voice_note(deps, entity.path)
    return SayResponse(
        success=True,
        audio_file_path=entity.path,
        message=(
            "Generated and delivered the voice note."
            if delivered
            else "Generated speech audio."
        ),
    )
