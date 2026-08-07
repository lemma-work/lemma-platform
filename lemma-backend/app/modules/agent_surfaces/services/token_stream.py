"""Buffering model tokens on their way to a streaming surface.

Kept apart from the observer because the policy here is self-contained: what is
safe to show (reasoning is not), and when to actually spend an API call. The
observer owns the run lifecycle; this owns the bytes.
"""

from __future__ import annotations

import time

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.modules.agent.contracts import AgentEvent, Conversation
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.services.progress_events import _surface_platform

logger = get_logger(__name__)

# Batch deltas so a fast model does not spend the Slack rate limit one word at a
# time, while staying frequent enough to read as live.
_TOKEN_FLUSH_CHARS = 280
_TOKEN_FLUSH_INTERVAL_SECONDS = 0.8


class TokenStreamMixin:
    """The token-streaming half of the progress observer."""

    async def _open_stream(self, conversation: Conversation) -> None:
        """Open the live stream before any text exists."""
        if self._progress_handle is not None:
            return
        try:
            async with self.uow_factory() as uow:
                service = self.service_factory(uow)
                handle = await service.append_stream_text_for_conversation(
                    conversation_id=conversation.id,
                    progress_handle=None,
                    text="",
                )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.progress_observer.surface_token_flush_conversation.diagnostic'
            )
            return
        if handle is not None:
            self._progress_handle = handle

    async def _on_token(self, event: AgentEvent, conversation: Conversation) -> None:
        """Stream the answer as it is written, on platforms that can show it.

        Only ``text`` deltas: ``thinking`` deltas are model reasoning and must
        never reach a surface — the same rule ``sanitize_user_visible_text``
        enforces on every other path.
        """
        capabilities = PLATFORM_CAPABILITIES.get(_surface_platform(conversation) or "")
        if capabilities is None or not capabilities.finishes_stream_with_answer:
            return
        payload = event.data if isinstance(event.data, dict) else {}
        if str(payload.get("kind") or "") != "text":
            return
        delta = str(payload.get("data") or "")
        if not delta:
            return
        visible = self._think_filter.feed(delta)
        if not visible:
            return
        self._token_buffer += visible
        now = time.monotonic()
        if (
            len(self._token_buffer) < _TOKEN_FLUSH_CHARS
            and (now - self._last_token_flush) < _TOKEN_FLUSH_INTERVAL_SECONDS
        ):
            return
        await self._flush_tokens(conversation)

    async def _flush_tokens(
        self, conversation: Conversation, *, final: bool = False
    ) -> None:
        if final:
            self._token_buffer += self._think_filter.flush()
        pending = self._token_buffer
        if not pending:
            return
        self._token_buffer = ""
        self._last_token_flush = time.monotonic()
        try:
            async with self.uow_factory() as uow:
                service = self.service_factory(uow)
                handle = await service.append_stream_text_for_conversation(
                    conversation_id=conversation.id,
                    progress_handle=self._progress_handle,
                    text=pending,
                )
        except SQLAlchemyError:
            logger.debug(
                'agent_surfaces.progress_observer.surface_token_flush_conversation.diagnostic'
            )
            return
        if handle is not None:
            self._progress_handle = handle
        self._streamed_text += pending

