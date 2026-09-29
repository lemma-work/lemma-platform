"""The Slack streaming lifecycle: open, append, close.

Split out of :mod:`service` because a stream is stateful in a way single-shot
sends are not — it is one message that stays open across a whole run, and every
call has to agree on its mode.
"""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import StreamAppendResult
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.rendering import chunk_text

from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import _markdown_chunk
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_customized_message_kwargs,
    slack_scopes,
)

logger = get_logger(__name__)


def _slack_error_code(exc: BaseException) -> str:
    """Slack's own error code when it sent one, else the exception's class."""
    if isinstance(exc, SlackApiError):
        return str((exc.response or {}).get("error") or "unknown")
    return type(exc).__name__


async def remove_processing_reaction(
    credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
) -> None:
    """Take the "eyes" back off the message the agent has now answered.

    ``add_processing_indicator`` puts the reaction on the inbound message as a
    "working on it" sign wherever the assistant status API is not available (any
    channel, and DMs without the scope). Nothing ever removed it, so every
    message the agent had answered stayed marked as being looked at. Best-effort:
    a reaction that is already gone, or a scope that was revoked, is not worth
    failing the reply that has just been delivered.
    """
    token = slack_access_token(credentials)
    channel = event.reply_target.get("channel")
    timestamp = event.external_message_id
    if not token or not channel or not timestamp:
        return
    if (
        event.is_dm
        and event.reply_target.get("thread_ts")
        and "assistant:write" in slack_scopes(credentials)
    ):
        # The status bubble clears itself when a message is posted; there was
        # no reaction to remove.
        return
    try:
        client = await build_slack_client(credentials)
        await client.reactions_remove(
            channel=str(channel), name="eyes", timestamp=str(timestamp)
        )
    except PLATFORM_TRANSPORT_ERRORS:
        logger.debug(
            "agent_surfaces.service.slack_processing_reaction_not_removed.diagnostic",
            exc_info=True,
        )


class SlackStreamSurface:
    """One live Slack message, written into across a run."""

    def __init__(self, *, credentials: dict[str, Any]) -> None:
        self.credentials = credentials

    async def append_stream_text(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> StreamAppendResult:
        """Append model text to a live stream, opening one if needed.

        This is what makes the answer *appear as it is written* rather than
        arriving whole. Opening lazily on the first token means a run that never
        produces text never leaves an empty stream behind, and no placeholder
        step is invented to justify the message existing.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        # An empty text is a request to *open* the stream (run start); only a
        # missing channel/thread makes it impossible.
        if not token or not channel or not thread_ts:
            return StreamAppendResult(handle=progress_handle, appended=False)
        if not text and progress_handle:
            return StreamAppendResult(handle=progress_handle, appended=False)
        client = await build_slack_client(self.credentials)
        try:
            if not (progress_handle and progress_handle.get("ts")):
                progress_handle = await self._open_stream(
                    client,
                    channel=str(channel),
                    thread_ts=str(thread_ts),
                    metadata=metadata,
                )
            if not text:
                return StreamAppendResult(handle=progress_handle, appended=False)
            await client.chat_appendStream(
                channel=str(progress_handle.get("channel") or channel),
                ts=str(progress_handle["ts"]),
                chunks=[_markdown_chunk(text)],
            )
            return StreamAppendResult(
                handle={**progress_handle, "streamed_text": True},
                appended=True,
            )
        except PLATFORM_TRANSPORT_ERRORS as exc:
            logger.debug(
                "agent_surfaces.service.slack_append_stream_text.diagnostic",
                error_code=_slack_error_code(exc),
                exc_info=True,
            )
            return StreamAppendResult(handle=progress_handle, appended=False)

    async def _open_stream(
        self,
        client: Any,
        *,
        channel: str,
        thread_ts: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Start a live stream on the thread, and describe it as a handle."""
        start_payload: dict[str, Any] = {
            "channel": channel,
            "thread_ts": thread_ts,
            # A stream is either chunk-based or plain-text for its whole life;
            # the answer rides as markdown chunks, so the stream opens in the
            # chunk mode or Slack rejects it as streaming_mode_mismatch.
            "task_display_mode": "timeline",
        }
        start_payload.update(
            slack_customized_message_kwargs(
                self.credentials,
                (metadata or {}).get("agent_display_name"),
                (metadata or {}).get("agent_icon_url"),
            )
        )
        response = await client.chat_startStream(**start_payload)
        return {
            "ts": str(response["ts"]),
            "channel": str(response.get("channel") or channel),
            "stream": True,
            "streamed_text": True,
        }

    async def finish_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Close the live stream *with* the final answer, as one message.

        This is the whole point of streaming: the thinking steps and the answer
        they produced are a single artifact in the channel, instead of a
        placeholder that gets deleted and an answer posted beside it.

        Returns False when there is no live stream to close, so the caller can
        deliver the answer as an ordinary message instead.
        """
        handle = progress_handle or {}
        channel = self._closable_stream_channel(event, handle, message)
        if not channel:
            return False
        # The answer must fit the 12k markdown budget; anything beyond it closes
        # the stream and continues as follow-up messages.
        chunks = chunk_text(message, limit=MARKDOWN_BLOCK_CHAR_LIMIT) or (
            [message] if message.strip() else []
        )
        if not await self._close_stream(handle, channel=channel, chunks=chunks):
            return False
        await self._send_overflow(event, chunks[1:], metadata=metadata)
        await remove_processing_reaction(self.credentials, event)
        return True

    def _closable_stream_channel(
        self, event: ParsedInboundSurfaceEvent, handle: dict[str, Any], message: str
    ) -> str:
        """The channel whose stream can be closed, or "" when none can be.

        A stream that already carries the answer still needs closing, so an
        empty message is only a refusal when nothing was streamed.
        """
        if not handle.get("ts") or not slack_access_token(self.credentials):
            return ""
        if not message.strip() and not handle.get("streamed_text"):
            return ""
        return str(handle.get("channel") or event.reply_target.get("channel") or "")

    async def _close_stream(
        self, handle: dict[str, Any], *, channel: str, chunks: list[str]
    ) -> bool:
        """Append the answer to the live stream, then stop it.

        Slack rejects a stopStream that tries to introduce the body itself, so
        append is the call that carries text and stop only finalises.
        """
        client = await build_slack_client(self.credentials)
        ts = str(handle["ts"])
        try:
            if chunks:
                await client.chat_appendStream(
                    channel=channel, ts=ts, chunks=[_markdown_chunk(chunks[0])]
                )
            await client.chat_stopStream(channel=channel, ts=ts)
        except PLATFORM_TRANSPORT_ERRORS as exc:
            # Say which Slack error it was: this path falls back to a plain
            # message, so without the code a failure here is invisible. A
            # warning, because the fallback is what puts the answer in front of
            # the person and it is not guaranteed to work either. Widened past
            # `SlackApiError`: a timeout escaped from here and skipped delivery.
            logger.warning(
                "agent_surfaces.service.slack_finish_progress_stop_stream.degraded",
                error_code=_slack_error_code(exc),
                exc_info=True,
            )
            return False
        return True

    async def _send_overflow(
        self,
        event: ParsedInboundSurfaceEvent,
        overflow: list[str],
        *,
        metadata: dict[str, Any] | None,
    ) -> None:
        """Anything past the stream's markdown budget, as ordinary messages."""
        if not overflow:
            return
        # Imported here rather than at module scope so the two surfaces stay
        # independent.
        from app.modules.agent_surfaces.platforms.slack.service import (
            SlackPlatformService,
        )

        sender = SlackPlatformService(credentials=self.credentials)
        for remainder in overflow:
            await sender.send_message(event=event, message=remainder, metadata=metadata)

    async def end_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Dispose of a live stream that will never receive an answer.

        The answer is going somewhere else (a plain message, or nowhere at all
        because the run failed), so leaving the stream behind strands a
        "Thinking…" bubble next to the real reply — two messages where the user
        should see one. Close it, then delete it.

        Deleting is best-effort on top of the close: the stream has to be
        stopped before Slack will accept a delete, and if the delete is refused
        a stopped stream is still better than a spinning one.
        """
        if not progress_handle or not progress_handle.get("ts"):
            return
        token = slack_access_token(self.credentials)
        if not token:
            return
        client = await build_slack_client(self.credentials)
        channel = progress_handle.get("channel") or event.reply_target.get("channel")
        try:
            if progress_handle.get("stream"):
                await client.chat_stopStream(
                    channel=str(channel), ts=str(progress_handle["ts"])
                )
            await client.chat_delete(
                channel=str(channel), ts=str(progress_handle["ts"])
            )
        except PLATFORM_TRANSPORT_ERRORS:
            logger.debug(
                "agent_surfaces.service.slack_end_progress_delete_channel.diagnostic",
                exc_info=True,
            )
