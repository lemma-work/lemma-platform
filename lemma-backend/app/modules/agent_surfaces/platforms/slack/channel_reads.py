"""Reading a Slack workspace back: channels, history, and search.

The half of the platform service that only ever asks Slack questions. It is
paged, budgeted and best-effort throughout, which is what separates it from
the sending half where a failure is the agent's problem to report.
"""

from typing import Any

from pydantic_ai.tools import RunContext
from slack_sdk.errors import SlackApiError

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceChannelInfo,
    SurfaceContextMessage,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    SlackSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.common import (
    background_channel_context_note,
    channel_author_label,
    payload_first,
    payload_text,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
)
from app.modules.agent_surfaces.platforms.slack.models import (
    SlackChannelMessageSnapshot,
    SlackFileAttachment,
    SlackRecentChannelMessagesParams,
    SlackRecentChannelMessagesResult,
    SlackSearchChannelMessagesParams,
    SlackSearchChannelMessagesResult,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _build_channel_history_kwargs(
    *,
    channel: str,
    limit: int,
    current_thread_id: str | None,
    include_current_thread: bool,
    cursor: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"channel": channel, "limit": limit}
    if cursor:
        kwargs["cursor"] = cursor
        return kwargs
    if current_thread_id and not include_current_thread and not channel.startswith("D"):
        kwargs["latest"] = current_thread_id
        kwargs["inclusive"] = False
    return kwargs


def _retryable_without_private_channels(exc: SlackApiError, channel_types: str) -> bool:
    """Whether this failure is the private-channel scope, and a retry can help."""
    error_code = payload_text(exc.response or {}, "error")
    if error_code != "missing_scope" or channel_types == "public_channel":
        return False
    logger.debug(
        "agent_surfaces.service.slack_list_channels_private_unavailable.diagnostic",
        error_code=error_code,
    )
    return True


def _channel_infos(response: Any) -> list[SurfaceChannelInfo]:
    """The channels on one page, skipping any entry with no id."""
    infos: list[SurfaceChannelInfo] = []
    for item in response.get("channels") or []:
        channel_id = payload_text(item, "id").strip()
        if channel_id:
            infos.append(
                SurfaceChannelInfo(
                    id=channel_id,
                    name=item.get("name"),
                    is_member=item.get("is_member"),
                )
            )
    return infos


def _next_cursor(response: Any) -> str | None:
    """The cursor for the next page, or None at the end of the list."""
    return (
        payload_text(response.get("response_metadata") or {}, "next_cursor").strip()
        or None
    )


def _context_messages(
    raw: list[Any], *, current_ts: str
) -> list[SurfaceContextMessage]:
    """Slack message dicts as context, without the message being handled."""
    out: list[SurfaceContextMessage] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = payload_text(item, "text").strip()
        ts = payload_text(item, "ts")
        if not text or (current_ts and ts == current_ts):
            continue
        out.append(
            SurfaceContextMessage(
                author=payload_first(item, "user", "username").strip() or None,
                text=text,
                ts=ts or None,
            )
        )
    return out


def _matches_in_page(
    batch: list[SlackChannelMessageSnapshot],
    *,
    query: str,
    wanted: int,
    budget: int,
) -> tuple[list[SlackChannelMessageSnapshot], int]:
    """Messages on this page containing the query, and how many were scanned.

    Scanning stops at whichever runs out first -- the matches still wanted, or
    the caller's scan budget -- so the count returned is what was actually read
    rather than the size of the page.
    """
    found: list[SlackChannelMessageSnapshot] = []
    scanned = 0
    for item in batch:
        scanned += 1
        if query in item.text.lower():
            found.append(item)
            if len(found) >= wanted:
                break
        if scanned >= budget:
            break
    return found, scanned


class SlackChannelReadsMixin:
    """The read-only half of :class:`SlackPlatformService`."""

    async def list_channels(self) -> list[SurfaceChannelInfo]:
        """List Slack public/private channels for configuring channel routes.

        Private channels need ``groups:read``, which a workspace installed
        before that scope shipped will not have granted. Slack answers such a
        request with ``missing_scope``, so the first failure retries with public
        channels only rather than leaving the picker empty.
        """
        client = await build_slack_client(self.credentials)
        channels: list[SurfaceChannelInfo] = []
        cursor: str | None = None
        channel_types = "public_channel,private_channel"
        for _ in range(20):  # bounded pagination safety
            try:
                response = await client.conversations_list(
                    types=channel_types,
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
            except SlackApiError as exc:
                if not _retryable_without_private_channels(exc, channel_types):
                    raise
                channel_types = "public_channel"
                continue
            channels.extend(_channel_infos(response))
            cursor = _next_cursor(response)
            if not cursor:
                break
        return channels

    async def get_recent_channel_messages(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: SlackRecentChannelMessagesParams,
    ) -> SlackRecentChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.deps.external_channel_id
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_get_recent_channel_messages.diagnostic",
                conversation_id=ctx.deps.conversation_id,
            )
            return SlackRecentChannelMessagesResult(
                success=False,
                error="Slack conversation context is missing channel credentials.",
            )

        try:
            client = await build_slack_client(self.credentials)
            response = await client.conversations_history(
                **_build_channel_history_kwargs(
                    channel=str(channel),
                    limit=request.limit,
                    current_thread_id=ctx.deps.external_thread_id,
                    include_current_thread=request.include_current_thread,
                )
            )
            messages = self._normalize_slack_messages(
                response.get("messages") or [],
                current_thread_id=ctx.deps.external_thread_id,
                include_current_thread=request.include_current_thread,
            )
            return SlackRecentChannelMessagesResult(
                success=True,
                message=background_channel_context_note(len(messages)),
                messages=messages,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_get_recent_channel_messages.propagated",
                conversation_id=ctx.deps.conversation_id,
                exc_info=True,
            )
            raise

    async def fetch_recent_context(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """Recent thread/channel messages for background context on a mention.

        Uses conversations.replies inside a thread, else conversations.history.
        Best-effort: missing creds / API errors yield an empty list.
        """
        token = slack_access_token(self.credentials)
        channel = event.external_channel_id
        if not token or not channel:
            return []
        try:
            raw = await self._recent_messages(
                channel=str(channel),
                thread_ts=event.external_thread_id,
                limit=limit,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_fetch_recent_context_channel.diagnostic"
            )
            return []
        return _context_messages(
            raw[-limit:], current_ts=str(event.external_message_id or "")
        )

    async def _recent_messages(
        self, *, channel: str, thread_ts: Any, limit: int
    ) -> list[Any]:
        """The thread's replies when we are inside one, else channel history."""
        client = await build_slack_client(self.credentials)
        if thread_ts and str(thread_ts) != str(channel):
            response = await client.conversations_replies(
                channel=channel, ts=str(thread_ts), limit=limit
            )
            return list(response.get("messages") or [])  # oldest-first
        response = await client.conversations_history(channel=channel, limit=limit)
        # history is newest-first → flip to chronological
        return list(reversed(response.get("messages") or []))

    async def search_current_channel(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: SlackSearchChannelMessagesParams,
    ) -> SlackSearchChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.deps.external_channel_id
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_search_current_channel_missing.diagnostic",
                conversation_id=ctx.deps.conversation_id,
            )
            return SlackSearchChannelMessagesResult(
                success=False,
                error="Slack conversation context is missing channel credentials.",
            )

        query = request.query.strip().lower()
        if not query:
            return SlackSearchChannelMessagesResult(
                success=False,
                error="Query cannot be empty.",
            )

        try:
            client = await build_slack_client(self.credentials)
            matches: list[SlackChannelMessageSnapshot] = []
            cursor: str | None = None
            remaining = request.scan_limit
            while remaining > 0 and len(matches) < request.limit:
                response = await client.conversations_history(
                    **_build_channel_history_kwargs(
                        channel=str(channel),
                        limit=min(100, remaining),
                        current_thread_id=ctx.deps.external_thread_id,
                        include_current_thread=request.include_current_thread,
                        cursor=cursor,
                    )
                )
                found, scanned = _matches_in_page(
                    self._normalize_slack_messages(
                        response.get("messages") or [],
                        current_thread_id=ctx.deps.external_thread_id,
                        include_current_thread=request.include_current_thread,
                    ),
                    query=query,
                    wanted=request.limit - len(matches),
                    budget=remaining,
                )
                matches.extend(found)
                remaining -= scanned
                cursor = _next_cursor(response)
                if not cursor:
                    break

            return SlackSearchChannelMessagesResult(
                success=True,
                message=background_channel_context_note(len(matches)),
                matches=matches,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_search_current_channel_channel.propagated",
                conversation_id=ctx.deps.conversation_id,
                exc_info=True,
            )
            raise

    def _normalize_slack_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        current_thread_id: str | None,
        include_current_thread: bool,
    ) -> list[SlackChannelMessageSnapshot]:
        normalized: list[SlackChannelMessageSnapshot] = []
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            parsed = (
                self.parser.normalize_context_message(item) if self.parser else None
            )
            if parsed is None:
                continue
            snapshot = SlackChannelMessageSnapshot.model_validate(parsed)
            if (
                not include_current_thread
                and current_thread_id
                and snapshot.thread_ts == current_thread_id
            ):
                continue
            if snapshot.author_label is None:
                snapshot.author_label = channel_author_label(
                    snapshot.display_name, snapshot.user
                )
            normalized.append(snapshot)
        return normalized

    def _current_message_attachments(
        self,
        ctx: RunContext[ConversationContext],
    ) -> list[SlackFileAttachment]:
        metadata = ctx.deps.surface_metadata
        if not isinstance(metadata, SlackSurfaceEventMetadata):
            return []
        return list(metadata.attachments)
