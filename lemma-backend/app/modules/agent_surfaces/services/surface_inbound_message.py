"""Recording what arrived, and folding in what arrived with it.

The write half of ingress: persist the inbound message, and enrich it from
things that are not the message itself -- a voice note that has to be
transcribed first, recent channel history a group mention needs for context.
"""

from __future__ import annotations

import asyncio
from typing import Any


from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service

from app.composition.surface_agent import ConversationService
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    # Re-exported: ``_ask_user_request_dict`` still has a caller here (the
    # native-interaction path) and a unit test that imports it from this module.
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15


def _speech_provider() -> Any:
    """The speech provider, or None when it cannot be reached."""
    try:
        from app.composition.surface_agent import get_speech_provider

        return get_speech_provider()
    except Exception as exc:
        # Voice notes arrive untranscribed from here on, which is a user-visible
        # degradation — so it stays a warning, but it has to name the failure.
        # The line this replaced carried no fields at all, so it could only
        # report that something was wrong.
        logger.warning(
            "agent_surfaces.ingress_service.speech_provider_unavailable",
            error_type=type(exc).__name__,
        )
        return None


def _record_transcripts(
    results: list[tuple[IngestedAttachment, Any]], metadata: dict[str, Any]
) -> list[str]:
    """The transcripts that came back, with provenance stamped into metadata."""
    transcripts: list[str] = []
    provenance: list[dict[str, Any]] = []
    for item, result in results:
        text = (getattr(result, "text", "") or "").strip()
        if not text:
            provenance.append({"path": item.path, "text": "", "failed": True})
            continue
        transcripts.append(text)
        provenance.append(
            {
                "path": item.path,
                "text": text,
                "detected_language": getattr(result, "detected_language", None),
                "duration_seconds": getattr(result, "duration_seconds", None),
            }
        )
    if provenance:
        metadata["voice_transcripts"] = provenance
    if not transcripts:
        metadata["voice_transcription_failed"] = True
    return transcripts


def _is_a_type_word(text: str, ingested: list[IngestedAttachment]) -> bool:
    """Is this "caption" only the parser's name for the kind of file it was?

    WhatsApp media carries its caption on the media object and often carries
    none at all, so the parser falls back to the type word -- "audio", "image" --
    to keep a media-only message from arriving as empty text and being dropped.
    That fallback is right where nothing else says anything, and wrong the moment
    a transcript does: every voice note reached the model as
    ``audio\n\n<what they said>``, which reads as the person having typed the
    word "audio" first. Seven such messages on dev, every one of them.

    Matched against what this message actually carried rather than a list of
    words, so a person who really did type "audio" alongside a photo keeps it.
    """
    lowered = text.strip().lower()
    return any(
        lowered == str(item.content_type or "").strip().lower() for item in ingested
    )


def _combined_voice_text(transcripts: list[str]) -> str:
    """One block of text from however many voice notes arrived.

    A voice-only message must never become an empty prompt, so a failed or
    empty transcription still says that something was said.
    """
    if not transcripts:
        return "[voice message]"
    if len(transcripts) == 1:
        return transcripts[0]
    return "\n\n".join(
        f"[Voice {index}]\n{text}" for index, text in enumerate(transcripts, start=1)
    )


class SurfaceInboundMessageMixin:
    async def _commit_inbound_message(
        self,
        context: SurfaceChatContext,
        message_text: str,
        metadata: dict[str, Any],
    ):
        """Persist the inbound message / resume the paused run in a short UoW."""
        if self._uow_factory is not None:
            if self._conversation_service_factory is None:
                raise RuntimeError("Conversation service factory is unavailable")
            async with self._uow_factory() as uow:
                conversation_service = self._conversation_service_factory(uow)
                return await self._write_inbound_message(
                    context, message_text, metadata, uow, conversation_service
                )
        else:
            if self.uow is None or self.conversation_service is None:
                raise RuntimeError("Conversation service is unavailable")
            return await self._write_inbound_message(
                context, message_text, metadata, self.uow, self.conversation_service
            )

    async def _write_inbound_message(
        self,
        context: SurfaceChatContext,
        message_text: str,
        metadata: dict[str, Any],
        uow,
        conversation_service: ConversationService,
    ):
        if context.pod_id is None:
            raise ValueError("Surface chat context requires a pod")
        # An empty inbound is never something a person sent — it means a body we
        # failed to fetch or parse. Starting a run on it burns a model call and
        # produces an answer to nothing, which reads to the sender as the agent
        # ignoring them. Every inbound Resend email looked like this.
        if not str(message_text or "").strip():
            logger.warning(
                "agent_surfaces.ingress_service.inbound_message_empty.degraded",
                conversation_id=str(context.conversation_id),
                platform=context.platform,
            )
            return None
        auth_ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=context.user_id,
            pod_id=context.pod_id,
        )
        token = set_current_context(auth_ctx)
        try:
            # If the run is paused on an ask_user, treat this inbound text as the
            # answer and resume — rather than starting a new message/run. This is
            # how the formatted-text fallback (and any "type your own" reply) gets
            # back into the run as a structured answer.
            if not await maybe_resume_pending_interaction(
                context, message_text, conversation_service=conversation_service
            ):
                return await conversation_service.add_user_message_and_start_run(
                    conversation_id=context.conversation_id,
                    user_id=context.user_id,
                    content=message_text,
                    pod_id=context.pod_id,
                    agent_name=context.agent_name,
                    message_metadata=metadata,
                )
            return None
        finally:
            reset_current_context(token)

    async def _fetch_channel_context(
        self,
        *,
        adapter: SurfacePlatformAdapterPort,
        context: SurfaceChatContext,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Best-effort recent thread/channel messages for a group mention, as a
        list of ``{author, text, ts}`` dicts. Fetched fresh per run; never raises."""
        try:
            messages = await adapter.fetch_thread_context(
                credentials=credentials,
                event=context.event,
                limit=_CHANNEL_CONTEXT_LIMIT,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.ingress_service.surface_channel_context_fetch_platform.diagnostic",
                conversation_id=context.conversation_id,
            )
            return []
        return [m.model_dump(mode="json") for m in messages][:_CHANNEL_CONTEXT_LIMIT]

    async def _transcribe_voice_attachments(
        self,
        *,
        ingested: list[IngestedAttachment],
        original_text: str | None,
        metadata: dict[str, Any],
    ) -> str:
        """Transcribe inbound voice notes and fold them into the message text.

        The transcript becomes the user's words so the agent just reads text.
        Join rules: caption + voice → both; voice-only → transcript alone;
        several voices → labelled concatenation. A failed/oversize/empty voice
        falls back to ``[voice message]`` (so a voice-only message is never an
        empty prompt) while the saved audio file stays available. Provenance
        (path + transcript + language) is recorded in ``metadata``.
        """
        original = (original_text or "").strip()
        if not any(item.is_audio for item in ingested):
            return original

        results = await self._transcribe_all(
            [
                item
                for item in ingested
                if item.is_audio and item.audio_bytes is not None
            ]
        )
        combined = _combined_voice_text(_record_transcripts(results, metadata))
        if original and not _is_a_type_word(original, ingested):
            return f"{original}\n\n{combined}"
        return combined

    async def _transcribe_all(
        self, items: list[IngestedAttachment]
    ) -> list[tuple[IngestedAttachment, Any]]:
        """Transcribe every voice note at once; a failure yields None for that one."""
        if not items:
            return []
        provider = _speech_provider()
        if provider is None:
            return []

        async def _one(item: IngestedAttachment) -> tuple[IngestedAttachment, Any]:
            try:
                return item, await provider.transcribe(
                    item.audio_bytes, mime=item.mime or "audio/ogg"
                )
            except Exception:
                return item, None

        return list(await asyncio.gather(*[_one(item) for item in items]))
