"""Recording what arrived, and folding in what arrived with it.

The write half of ingress: persist the inbound message, and enrich it from
things that are not the message itself -- a voice note that has to be
transcribed first, recent channel history a group mention needs for context.
"""

from __future__ import annotations

from typing import Any


from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service

from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.speech import (
    VoiceClip,
    VoiceTranscript,
    transcribe_voice_notes,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
)
from app.modules.agent_surfaces.domain.ports import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.services.pending_interaction_resume import (
    ResumeOutcome,
    maybe_resume_pending_interaction,
)
from app.modules.agent_surfaces.services.surface_file_ingest_service import (
    IngestedAttachment,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Recent thread/channel messages fetched per run for group-mention continuity.
_CHANNEL_CONTEXT_LIMIT = 15

# Said when a decision was understood but could not be written down. It has to
# state that nothing happened: the failure mode this replaced was an "approve"
# that silently became a denial, so "it didn't go through" is the one fact the
# person needs in order to know that answering again is safe.
_DECISION_NOT_RECORDED = (
    "I couldn't record that — nothing has been approved or denied, and the "
    "request is still waiting. Please answer again."
)


def _record_transcripts(
    results: list[tuple[IngestedAttachment, VoiceTranscript | None]],
    metadata: dict[str, Any],
) -> list[str]:
    """The transcripts that came back, with provenance stamped into metadata."""
    transcripts: list[str] = []
    provenance: list[dict[str, Any]] = []
    for item, result in results:
        text = (result.text if result else "").strip()
        if not text:
            provenance.append({"path": item.path, "text": "", "failed": True})
            continue
        transcripts.append(text)
        provenance.append(
            {
                "path": item.path,
                "text": text,
                "detected_language": result.detected_language,
                "duration_seconds": result.duration_seconds,
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
        """Persist the inbound message / resume the paused run in a short UoW.

        The two modes are now one line apart rather than two branches building
        two different collaborators: every conversation operation takes the unit
        of work, so the worker's short-scoped session and the request's
        long-lived one are the same argument.
        """
        if self._uow_factory is not None:
            async with self._uow_factory() as uow:
                return await self._write_inbound_message(
                    context, message_text, metadata, uow
                )
        if self.uow is None:
            raise RuntimeError("Surface ingress has no unit of work")
        return await self._write_inbound_message(
            context, message_text, metadata, self.uow
        )

    async def _write_inbound_message(
        self,
        context: SurfaceChatContext,
        message_text: str,
        metadata: dict[str, Any],
        uow,
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
            outcome = await maybe_resume_pending_interaction(
                context, message_text, uow=uow
            )
            if outcome is ResumeOutcome.FAILED:
                # They decided and we could not write it down. Starting a turn
                # here is what turned an "approve" into a cancellation: it
                # supersedes the pause with an auto-DENY. The pause is still
                # there, so saying so and letting them answer again is the one
                # move that loses nothing.
                await self._say_the_decision_was_not_recorded(context)
                return None
            if outcome is ResumeOutcome.NOT_A_DECISION:
                return await agent_conversations.start_surface_turn(
                    uow,
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

    async def _say_the_decision_was_not_recorded(
        self, context: SurfaceChatContext
    ) -> None:
        """Tell the person their answer did not land, so they can give it again.

        A bare envelope rather than ``send_agent_message_for_conversation``:
        that one drains the files ``display_resource`` is holding for a
        one-reply surface, and attaching them to an apology would consume them
        before the reply they were meant for. ``_deliver_envelope`` already
        reports a delivery that reached nobody, so there is nothing to catch
        here — and if resolving the target fails too, this inbound is failing
        loudly rather than quietly, which is the point.
        """
        target = await self._resolve_egress_target(context.conversation_id)
        if target is None:
            return
        await self._deliver_envelope(
            target,
            envelope=SurfaceEnvelope(text=_DECISION_NOT_RECORDED),
            metadata={},
            conversation_id=context.conversation_id,
        )

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
    ) -> list[tuple[IngestedAttachment, VoiceTranscript | None]]:
        """Transcribe every voice note at once; a failure yields None for that one."""
        transcripts = await transcribe_voice_notes(
            [
                VoiceClip(audio_bytes=item.audio_bytes, mime=item.mime or "audio/ogg")
                for item in items
            ]
        )
        return list(zip(items, transcripts))
