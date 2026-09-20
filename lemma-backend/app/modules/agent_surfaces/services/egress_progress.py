"""Driving a surface's live message while a run is in flight.

These five are the conversation-level face of a platform's *editable message*
API -- open a live message, write into it, close it with the answer, dispose of
it, show a typing bubble. That is a different adapter API from the one
:class:`SurfaceEgress` speaks, which is ``deliver(envelope)``: an envelope is a
thing a person receives, and everything here edits something they are already
looking at. Nothing here builds an envelope, and nothing in
:class:`SurfaceEgress` edits a live message, which is why they are two objects
over one :class:`SurfaceDelivery` rather than one object with two halves.

Stateless on purpose: the run owns the progress handle and passes it in. Every
method here is best-effort -- a dropped progress edit must never take down a
run, and the answer still lands through the egress path.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.models import StreamAppendResult
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text
from app.modules.agent_surfaces.services.egress_delivery import SurfaceDelivery

logger = get_logger(__name__)


class SurfaceProgress:
    """The live-message half of egress."""

    def __init__(self, *, delivery: SurfaceDelivery) -> None:
        self.delivery = delivery

    async def send_progress_update(
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
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            # Silent until this event existed, and indistinguishable from a
            # platform that simply has no live progress -- so a conversation
            # that could not resolve its surface just never showed progress and
            # said nothing about why.
            logger.debug(
                "agent_surfaces.egress.progress_no_target.diagnostic",
                conversation_id=conversation_id,
            )
            return progress_handle
        try:
            # Author the stream as the agent: the answer that closes this same
            # message carries the agent's name, so the stream must too or the
            # thread reads as two different speakers.
            metadata = await self.delivery.egress_metadata(target)
            # No connection held for the platform call; see `connection_released`.
            async with connection_released(self.delivery.uow.session):
                return await target.adapter.stream_progress(
                    credentials=target.credentials,
                    event=target.event,
                    progress_text=progress_text,
                    progress_handle=progress_handle,
                    metadata=metadata,
                )
        except Exception:
            logger.debug(
                "agent_surfaces.egress.progress_update_failed.diagnostic",
                conversation_id=conversation_id,
            )
            return progress_handle

    async def append_streamed_text(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None,
        text: str,
    ) -> StreamAppendResult:
        """Append streamed model text; returns the (possibly new) handle.

        Best-effort by construction -- a dropped delta must never take down a
        run, and the final answer still lands through the normal path.
        """
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return StreamAppendResult(handle=progress_handle, appended=False)
        try:
            metadata = await self.delivery.egress_metadata(target)
            # No connection held for the platform call; see `connection_released`.
            async with connection_released(self.delivery.uow.session):
                return await target.adapter.append_stream_text(
                    credentials=target.credentials,
                    event=target.event,
                    progress_handle=progress_handle,
                    text=text,
                    metadata=metadata,
                )
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.egress.progress_append_failed.diagnostic",
                conversation_id=conversation_id,
            )
            return StreamAppendResult(handle=progress_handle, appended=False)

    async def finish_with_answer(
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
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        clean_message = sanitize_user_visible_text(message)
        # An already-streamed answer legitimately has nothing left to send -- the
        # stream still has to be closed, or it spins forever.
        if not clean_message and not already_streamed:
            return False
        message_metadata = await self.delivery.egress_metadata(target, metadata)
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(self.delivery.uow.session):
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
                    "agent_surfaces.egress.progress_finish_failed.diagnostic",
                    conversation_id=conversation_id,
                )
                return False

    async def clear_progress(
        self,
        *,
        conversation_id: UUID,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Remove the streaming progress message at run end (best-effort)."""
        if not progress_handle:
            return
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(self.delivery.uow.session):
            try:
                await target.adapter.end_progress(
                    credentials=target.credentials,
                    event=target.event,
                    progress_handle=progress_handle,
                )
            except Exception:
                logger.debug(
                    "agent_surfaces.egress.progress_clear_failed.diagnostic",
                    conversation_id=conversation_id,
                )

    async def show_typing(
        self,
        *,
        conversation_id: UUID,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """The platform's own "working on it" affordance, where it has one."""
        target = await self.delivery.resolve_egress_target(conversation_id)
        if target is None:
            return False
        indicator_metadata = await self.delivery.egress_metadata(target, metadata)
        # No connection held for the platform call; see `connection_released`.
        async with connection_released(self.delivery.uow.session):
            await target.adapter.add_processing_indicator(
                credentials=target.credentials,
                event=target.event,
                metadata=indicator_metadata,
            )
            return True
