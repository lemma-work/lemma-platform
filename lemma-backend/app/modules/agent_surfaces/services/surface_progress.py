"""Driving a surface's live progress message for one conversation.

These four are the conversation-level face of a platform's streaming API: open
it, write into it, close it with the answer, or dispose of it. Kept together and
apart from :mod:`ingress_service` because they share one lifecycle — a single
message that stays open across a whole run.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text

logger = get_logger(__name__)


class SurfaceProgressMixin:
    """The live-progress half of the ingress service."""

    async def send_progress_update_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Stream a live progress line on platforms with editable messages.

        Best-effort: returns the (possibly updated) handle and never raises, so a
        failed progress edit cannot affect the agent run.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return progress_handle
        try:
            # Author the stream as the agent: the answer that closes this same
            # message carries the agent's name, so the stream must too or the
            # thread reads as two different speakers.
            metadata = await self._egress_metadata_with_agent_name(target, None)
            return await target.adapter.stream_progress(
                credentials=target.credentials,
                event=target.event,
                progress_text=progress_text,
                progress_handle=progress_handle,
                metadata=metadata,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_update_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return progress_handle

    async def append_stream_text_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None,
        text: str,
    ) -> dict[str, Any] | None:
        """Append streamed model text; returns the (possibly new) handle.

        Best-effort by construction — a dropped delta must never take down a
        run, and the final answer still lands through the normal path.
        """
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return progress_handle
        try:
            metadata = await self._egress_metadata_with_agent_name(target, None)
            return await target.adapter.append_stream_text(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
                text=text,
                metadata=metadata,
            )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_stream_text_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return progress_handle

    async def finish_progress_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
        already_streamed: bool = False,
    ) -> bool:
        """Close a live progress stream with the final answer, as one message.

        Returns False when the platform cannot do this (every platform except
        Slack today) or the attempt failed, so the caller falls back to clearing
        progress and sending the answer separately.
        """
        if not progress_handle:
            return False
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return False
        clean_message = sanitize_user_visible_text(message)
        # An already-streamed answer legitimately has nothing left to send — the
        # stream still has to be closed, or it spins forever.
        if not clean_message and not already_streamed:
            return False
        message_metadata = await self._egress_metadata_with_agent_name(target, metadata)
        try:
            return await target.adapter.finish_progress(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
                message=clean_message,
                metadata=message_metadata,
            )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_finish_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )
            return False

    async def clear_progress_for_conversation(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Remove the streaming progress message at run end (best-effort)."""
        if not progress_handle:
            return
        target = await self._resolve_egress_target(conversation_id)
        if target is None:
            return
        try:
            await target.adapter.end_progress(
                credentials=target.credentials,
                event=target.event,
                progress_handle=progress_handle,
            )
        except Exception:
            logger.debug(
                'agent_surfaces.ingress_service.surface_progress_clear_conversation_s.diagnostic',
                conversation_id=conversation_id,
            )

