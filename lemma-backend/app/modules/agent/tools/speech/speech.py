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


def _both_spellings_of(deps: BaseAgentContext, path: str) -> tuple[str, ...]:
    """The datastore path as stored and as a person would write it.

    ``/me/whatsapp/audio.ogg`` and ``/{user_id}/whatsapp/audio.ogg`` are the same
    file: the first is what a listing shows, the second is what is stored and
    what the surface prompt block quotes. Either can arrive here.
    """
    personal_root = f"/{deps.user_id}"
    if path.startswith("/me/"):
        return (path, f"{personal_root}{path.removeprefix('/me')}")
    if path.startswith(f"{personal_root}/"):
        return (path, f"/me{path.removeprefix(personal_root)}")
    return (path,)


async def _already_transcribed(deps: BaseAgentContext, path: str) -> str | None:
    """What ingress already made of this file, if it made anything.

    Best effort by construction: a lookup that fails must fall through to a real
    transcription rather than fail the call, because being slow and expensive is
    better than refusing to answer.

    Narrow on purpose. This is one indexed SELECT in a session of its own, so a
    database error is the whole of what it can fail with -- the same reasoning
    `PendingUserMessagesCapability._claim` states for the same shape. Anything
    else raised here is a bug and should surface rather than quietly cost the
    caller a second transcription.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.infrastructure.repositories import ConversationRepository

    # A transcript belongs to a conversation, so without one there is nothing to
    # look in. Every real run has one; this keeps the tool callable from the
    # paths that build a leaner context rather than making them carry a field
    # only this lookup reads.
    conversation_id = getattr(deps, "conversation_id", None)
    if conversation_id is None:
        return None
    try:
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            return await ConversationRepository(uow).find_existing_voice_transcript(
                conversation_id, _both_spellings_of(deps, path)
            )
    except SQLAlchemyError:
        logger.warning(
            "agent.speech.transcript_reuse_lookup_failed.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return None


async def listen_internal(
    deps: BaseAgentContext, request: ListenRequest
) -> ListenResponse:
    path = (request.file_path or "").strip()
    if not path:
        return ListenResponse(success=False, error="file_path is required.")

    # Answered from what the run was already given, before a provider is
    # reached. A voice note is transcribed at ingress and its words arrive as
    # the message text; the prompt says so and says not to call this, and
    # sometimes the model calls it anyway. Telling it again would not help --
    # what stops the second bill is there being nothing to bill for.
    reused = await _already_transcribed(deps, path)
    if reused is not None:
        logger.info(
            "agent.speech.transcript_reused.observed",
            conversation_id=str(getattr(deps, "conversation_id", None)),
        )
        return ListenResponse(
            success=True,
            transcript=reused,
            message=(
                "This voice note was transcribed when it arrived; these are the "
                "same words already in the message above. Nothing was "
                "re-transcribed."
            ),
        )

    try:
        content, mime = await read_agent_file_bytes(deps, path)
    except FileNotFoundError:
        return ListenResponse(success=False, error=f"File not found: {path}")
    except Exception as exc:
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
    from app.modules.agent_surfaces.contracts.platforms import voice_note_format

    return voice_note_format(platform)


async def _deliver_voice_note(deps: BaseAgentContext, path: str) -> bool:
    """Get the audio to the person, however this surface delivers.

    Branching by the platform's delivery cardinality rather than by whether it
    happens to be email, which is what `display_resource` already does:

    * MANY (Slack/Teams/Telegram/WhatsApp) — deliver now, as a voice note where
      the platform has them and as an audio file where it does not.
    * ONE (email) — hold it for the single reply, which drains what was held
      when it sends.
    * not a surface run (web/app/subagent) — nothing to deliver to; the file
      path is the answer, and the player is in the workspace.

    That middle branch used to `return False` here, on the reasoning that "email
    composes one reply via the reply tool; the agent attaches the audio there".
    There is no reply tool any more — the run observer sends the one reply — so
    the audio was synthesized, billed, written to the pod, and never delivered,
    while `say` told the model it had spoken.
    """
    platform = getattr(deps, "surface_platform", None)
    conversation_id = getattr(deps, "conversation_id", None)
    if not platform or not conversation_id:
        return False
    from app.modules.agent_surfaces.contracts.platforms import (
        platform_delivers_one_reply,
        platform_supports_chat_delivery,
    )

    if platform_delivers_one_reply(platform):
        from app.modules.agent_surfaces.contracts.egress import (
            hold_display_for_one_reply,
        )

        # Attached to the reply rather than sent as a second one: a surface that
        # gets one message gets one message, audio included.
        return hold_display_for_one_reply(conversation_id, path)
    if not platform_supports_chat_delivery(platform):
        return False
    try:
        from app.modules.agent_surfaces.contracts.egress import deliver_voice_note

        return await deliver_voice_note(conversation_id=conversation_id, file_path=path)
    except Exception:
        # The caller falls back to text, so the user still hears back -- but a
        # surface that has stopped accepting voice notes is worth seeing.
        logger.warning(
            "agent.speech.voice_note_delivery_failed.degraded",
            platform=str(platform),
            exc_info=True,
        )
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
            text,
            voice=request.voice,
            output_format=output_format,
            language=request.language,
        )
    except Exception as exc:
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
