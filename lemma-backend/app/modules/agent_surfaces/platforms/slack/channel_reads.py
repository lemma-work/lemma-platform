"""Reading a Slack workspace back: channels, history, and search.

The half of the platform service that only ever asks Slack questions. It is
paged, budgeted and best-effort throughout, which is what separates it from
the sending half where a failure is the agent's problem to report.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any

from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error

from app.modules.agent.contracts import ToolContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceChannelInfo,
    SurfaceContextMessage,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    SlackSurfaceEventMetadata,
)
from app.modules.agent.contracts.surface_prompts import (
    background_channel_context_note,
)
from app.modules.agent_surfaces.platforms.common import (
    platform_transport_errors,
    UnsafeApiBaseError,
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

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

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


async def _display_name(client: AsyncWebClient, user_id: str) -> str | None:
    """A Slack user's shown name, or None when Slack will not say."""
    try:
        response = await client.users_info(user=user_id)
    except (slack_api_error(), *platform_transport_errors()):
        logger.debug(
            "agent_surfaces.service.slack_get_user_display_name.observed",
            user_id=user_id,
        )
        return None
    profile = (response.get("user") or {}).get("profile") or {}
    name = str(profile.get("display_name") or profile.get("real_name") or "")
    return name.strip() or None


def _unnamed_authors(messages: list[SurfaceContextMessage]) -> list[str]:
    """The people whose lines still show a bare Slack id, apps left out."""
    return sorted(
        {
            message.author_external_id
            for message in messages
            if message.author_external_id
            and message.author == message.author_external_id
            and not message.from_bot
        }
    )


async def name_authors(
    messages: list[SurfaceContextMessage],
    *,
    lookup: Callable[[str], Awaitable[str | None]],
) -> list[SurfaceContextMessage]:
    """Put a name on the lines whose author is still a bare Slack id.

    A message carries its author's profile only some of the time, and a roster
    that reads ``"U0ABC123" is in this pod`` is nothing a model can match to the
    people in the thread. One lookup per person still unnamed, at most a
    window's worth; one that finds nothing leaves the id, no worse than before.
    """
    ordered = _unnamed_authors(messages)
    found = await asyncio.gather(*(lookup(user_id) for user_id in ordered))
    names = {user_id: name for user_id, name in zip(ordered, found) if name}
    return [
        message.model_copy(update={"author": names[message.author]})
        if message.author in names and message.author == message.author_external_id
        else message
        for message in messages
    ]


def _context_messages(
    raw: list[Any], *, current_ts: str, home_team: str = ""
) -> list[SurfaceContextMessage]:
    """Slack message dicts as context, without the message being handled.

    A message from another workspace -- a Slack Connect channel's other company
    -- is marked ``outside_pod``, the same mark the pod's own group log puts on
    a stranger's line, so a member's run can leave it out. That mark is the only
    one Slack makes, and it says nothing about somebody in the pod's own
    workspace, so the sender's id is kept beside the name: the roster a member's
    run is handed asks the pod whether that id is one of its own people
    (``services/group_log.pod_members_in_lines``).

    The author is the name the message carries (``user_profile``) where it has
    one, and the bare id otherwise -- ``name_authors`` then asks Slack for the
    rest. A message an app posted, the bot's own
    answers among them, is marked as one: it is nobody to put on a roster.
    """
    out: list[SurfaceContextMessage] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = payload_text(item, "text").strip()
        ts = payload_text(item, "ts")
        if not text or (current_ts and ts == current_ts):
            continue
        theirs = (payload_text(item, "user_team") or payload_text(item, "team")).strip()
        profile = item.get("user_profile")
        shown = (
            payload_first(profile, "display_name", "real_name").strip()
            if isinstance(profile, dict)
            else ""
        )
        out.append(
            SurfaceContextMessage(
                author=shown or payload_first(item, "user", "username").strip() or None,
                author_external_id=payload_text(item, "user").strip() or None,
                text=text,
                ts=ts or None,
                outside_pod=bool(home_team and theirs and theirs != home_team),
                from_bot=bool(
                    payload_text(item, "bot_id")
                    or payload_text(item, "subtype") == "bot_message"
                ),
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
            except slack_api_error() as exc:
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
        ctx: ToolContext,
        request: SlackRecentChannelMessagesParams,
    ) -> SlackRecentChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.external_channel_id
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_get_recent_channel_messages.diagnostic",
                conversation_id=ctx.conversation_id,
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
                    current_thread_id=ctx.external_thread_id,
                    include_current_thread=request.include_current_thread,
                )
            )
            messages = self._normalize_slack_messages(
                response.get("messages") or [],
                current_thread_id=ctx.external_thread_id,
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
                conversation_id=ctx.conversation_id,
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
        messages = _context_messages(
            raw[-limit:],
            current_ts=str(event.external_message_id or ""),
            home_team=str(event.tenant_id or ""),
        )
        if not _unnamed_authors(messages):
            return messages
        try:
            client = await build_slack_client(self.credentials)
            return await name_authors(messages, lookup=partial(_display_name, client))
        except (
            slack_api_error(),
            UnsafeApiBaseError,
            *platform_transport_errors(),
        ) as exc:
            # Naming is a nicety on history already in hand; losing it is no
            # reason to lose the history.
            logger.warning(
                "agent_surfaces.service.slack_name_authors_failed.degraded",
                error_type=type(exc).__name__,
            )
            return messages

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
        ctx: ToolContext,
        request: SlackSearchChannelMessagesParams,
    ) -> SlackSearchChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.external_channel_id
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_search_current_channel_missing.diagnostic",
                conversation_id=ctx.conversation_id,
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
                        current_thread_id=ctx.external_thread_id,
                        include_current_thread=request.include_current_thread,
                        cursor=cursor,
                    )
                )
                found, scanned = _matches_in_page(
                    self._normalize_slack_messages(
                        response.get("messages") or [],
                        current_thread_id=ctx.external_thread_id,
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
                conversation_id=ctx.conversation_id,
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
        ctx: ToolContext,
    ) -> list[SlackFileAttachment]:
        metadata = ctx.surface_metadata
        if not isinstance(metadata, SlackSurfaceEventMetadata):
            return []
        return list(metadata.attachments)
