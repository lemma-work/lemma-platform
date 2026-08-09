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
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
    truncate_slack_text as _truncate_slack_text,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_customized_message_kwargs,
)

logger = get_logger(__name__)


def _markdown_chunk(text: str) -> dict[str, Any]:
    """Model text as a stream chunk.

    A stream is either chunk-based or plain-text for its whole life. Because the
    step timeline uses chunks, the answer must be a chunk too — appending
    top-level ``markdown_text`` to a chunk stream is rejected with
    ``streaming_mode_mismatch``.
    """
    return {"type": "markdown_text", "text": text}


def _task_chunk(sequence: int, title: str | None, status: str) -> dict[str, Any]:
    """One step of the agent's work, as a Slack ``task_update`` chunk.

    The id is stable per step so appending the same id with ``complete`` closes
    the step already on screen rather than adding a second one.
    """
    return {
        "type": "task_update",
        "id": f"step-{sequence}",
        "title": _truncate_slack_text(str(title or "Working…"), 200) or "Working…",
        "status": status,
    }


class SlackStreamSurface:
    """One live Slack message, written into across a run."""

    def __init__(self, *, credentials: dict[str, Any]) -> None:
        self.credentials = credentials

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Open (or extend) a native Slack stream carrying the agent's steps.

        The first call opens a stream with ``chat.startStream``; each later call
        completes the step in flight and appends the next one as a
        ``task_update`` chunk, so Slack renders a collapsible timeline of what
        the agent actually did. ``finish_progress`` closes the same message with
        the final answer, which is why nothing here is ever deleted.

        Best-effort: rate limits / API errors keep the prior handle, and the
        caller falls back to posting the answer as its own message.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        # ``chat.startStream`` is thread-scoped. The parser always sets
        # thread_ts (falling back to the message ts), so this holds in channels
        # and DMs alike — but never stream without one.
        if not token or not channel or not thread_ts:
            return progress_handle
        client = build_slack_client(self.credentials)
        title = _truncate_slack_text(progress_text.strip(), 200) or "Working…"
        try:
            if progress_handle and progress_handle.get("ts"):
                sequence = int(progress_handle.get("task_seq") or 0)
                chunks: list[dict[str, Any]] = []
                if sequence:
                    chunks.append(
                        _task_chunk(sequence, progress_handle.get("task_title"), "complete")
                    )
                sequence += 1
                chunks.append(_task_chunk(sequence, title, "in_progress"))
                await client.chat_appendStream(
                    channel=str(progress_handle.get("channel") or channel),
                    ts=str(progress_handle["ts"]),
                    chunks=chunks,
                )
                return {
                    **progress_handle,
                    "task_seq": sequence,
                    "task_title": title,
                }
            start_payload: dict[str, Any] = {
                "channel": str(channel),
                "thread_ts": str(thread_ts),
                "task_display_mode": "timeline",
            }
            start_payload.update(
                slack_customized_message_kwargs(
                    self.credentials, (metadata or {}).get("agent_display_name")
                )
            )
            response = await client.chat_startStream(**start_payload)
            ts = str(response["ts"])
            resolved_channel = str(response.get("channel") or channel)
            await client.chat_appendStream(
                channel=resolved_channel,
                ts=ts,
                chunks=[_task_chunk(1, title, "in_progress")],
            )
            return {
                "ts": ts,
                "channel": resolved_channel,
                "stream": True,
                "task_seq": 1,
                "task_title": title,
            }
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_stream_progress_channel_s.diagnostic'
            )
            return progress_handle

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
        client = build_slack_client(self.credentials)
        try:
            if not (progress_handle and progress_handle.get("ts")):
                start_payload: dict[str, Any] = {
                    "channel": str(channel),
                    "thread_ts": str(thread_ts),
                    # Same mode the step stream uses. A stream is either
                    # chunk-based or plain-text for its whole life; mixing the
                    # two is what Slack rejects as streaming_mode_mismatch.
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
                progress_handle = {
                    "ts": str(response["ts"]),
                    "channel": str(response.get("channel") or channel),
                    "stream": True,
                    "task_seq": 0,
                    "streamed_text": True,
                }
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
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_append_stream_text.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return StreamAppendResult(handle=progress_handle, appended=False)

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
        if not progress_handle or not progress_handle.get("ts"):
            return False
        token = slack_access_token(self.credentials)
        # A stream that already carries the answer still needs closing, so an
        # empty message is only a refusal when nothing was streamed.
        if not token or (not message.strip() and not progress_handle.get("streamed_text")):
            return False
        client = build_slack_client(self.credentials)
        channel = str(
            progress_handle.get("channel") or event.reply_target.get("channel") or ""
        )
        if not channel:
            return False
        # The answer must fit the 12k markdown budget; anything beyond it closes
        # the stream and continues as follow-up messages.
        chunks_of_answer = chunk_text(message, limit=MARKDOWN_BLOCK_CHAR_LIMIT) or (
            [message] if message.strip() else []
        )
        sequence = int(progress_handle.get("task_seq") or 0)
        closing_chunks: list[dict[str, Any]] = []
        if sequence:
            closing_chunks.append(
                _task_chunk(sequence, progress_handle.get("task_title"), "complete")
            )
        try:
            # The answer is *appended* and the stream then closed. Slack rejects
            # a stopStream that tries to introduce the body itself, so append is
            # the call that carries text and stop only finalises.
            if chunks_of_answer or closing_chunks:
                append_kwargs: dict[str, Any] = {
                    "channel": channel,
                    "ts": str(progress_handle["ts"]),
                }
                combined = list(closing_chunks)
                if chunks_of_answer:
                    combined.append(_markdown_chunk(chunks_of_answer[0]))
                if combined:
                    append_kwargs["chunks"] = combined
                    await client.chat_appendStream(**append_kwargs)
            await client.chat_stopStream(
                channel=channel,
                ts=str(progress_handle["ts"]),
            )
        except SlackApiError as exc:
            # Say which Slack error it was: this path silently falls back to a
            # plain message, so without the code a failure here is invisible.
            logger.debug(
                'agent_surfaces.service.slack_finish_progress_stop_stream.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False
        overflow = chunks_of_answer[1:] if chunks_of_answer else []
        if overflow:
            # Anything past the stream's markdown budget continues as ordinary
            # messages. Imported here rather than at module scope so the two
            # surfaces stay independent.
            from app.modules.agent_surfaces.platforms.slack.service import (
                SlackPlatformService,
            )

            sender = SlackPlatformService(credentials=self.credentials)
            for remainder in overflow:
                await sender.send_message(
                    event=event, message=remainder, metadata=metadata
                )
        return True

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
        client = build_slack_client(self.credentials)
        channel = progress_handle.get("channel") or event.reply_target.get("channel")
        sequence = int(progress_handle.get("task_seq") or 0)
        try:
            if progress_handle.get("stream"):
                await client.chat_stopStream(
                    channel=str(channel),
                    ts=str(progress_handle["ts"]),
                    chunks=(
                        [
                            _task_chunk(
                                sequence, progress_handle.get("task_title"), "complete"
                            )
                        ]
                        if sequence
                        else None
                    ),
                )
            await client.chat_delete(
                channel=str(channel), ts=str(progress_handle["ts"])
            )
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_end_progress_delete_channel.diagnostic'
            )
