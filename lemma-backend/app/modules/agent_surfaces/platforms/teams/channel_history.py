"""Reading a Teams channel's recent messages back out of Graph.

Two callers want the same history for different reasons: the agent asking for it
as a tool, and the surface fetching background context on a mention. They differ
only in what a failure means -- a message the agent can read, or silence -- so
they share everything up to that point and part company at the end.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import aiohttp
from pydantic_ai.tools import RunContext

from app.core.log.log import get_logger
from app.core.net.aiohttp_client import new_aiohttp_session
from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import SurfaceContextMessage
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    build_surface_event_metadata,
)
from app.modules.agent_surfaces.platforms.common import (
    background_channel_context_note,
    channel_author_label,
    payload_section,
    payload_text,
)
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.credentials import (
    tenant_id_from_credentials,
)
from app.modules.agent_surfaces.platforms.teams.models import (
    TeamsChannelMessageSnapshot,
    TeamsGetRecentMessagesParams,
    TeamsGetRecentMessagesResult,
    TeamsMessageAttachmentSnapshot,
)
from app.modules.agent_surfaces.platforms.teams.parser import strip_html

logger = get_logger(__name__)


class _TeamsHistoryUnavailable(Exception):
    """Why a channel-history read cannot proceed, phrased for the agent.

    Each precondition on the way to a Graph call has its own explanation and
    they all end the same way -- an unsuccessful result carrying that sentence
    -- so they raise it rather than each assembling the result themselves.
    """


@dataclass(frozen=True, slots=True)
class _ChannelHistoryTarget:
    """The Graph coordinates of a channel-history read, once resolved."""

    token: str
    team_id: str
    team_aad_group_id: str | None
    service_url: str | None
    channel_id: str
    current_thread: str | None

    @property
    def thread_id(self) -> str | None:
        """The thread to read, or None when this conversation is the channel.

        Teams sets ``external_thread_id`` to the channel id for a message that
        is not in a thread, so the two being equal means there is no thread.
        """
        if self.current_thread and str(self.current_thread) != str(self.channel_id):
            return str(self.current_thread)
        return None


def _channel_messages_url(
    graph_base: str,
    *,
    graph_team_id: str,
    channel_id: str,
    thread_id: str | None,
    limit: int,
) -> str:
    """The Graph endpoint for a channel's messages, or for one thread's replies."""
    base = (
        f"{graph_base}/teams/{quote(str(graph_team_id))}"
        f"/channels/{quote(str(channel_id))}/messages"
    )
    if thread_id is None:
        return f"{base}?$top={limit}"
    return f"{base}/{quote(str(thread_id))}/replies?$top={limit}"


def _graph_snapshots(data: Any) -> Iterator[TeamsChannelMessageSnapshot]:
    """Graph channel items as snapshots, oldest first, skipping what will not parse."""
    for item in reversed((data or {}).get("value") or []):
        if not isinstance(item, dict):
            continue
        snapshot = _message_snapshot_from_graph_item(item)
        if snapshot is not None:
            yield snapshot


def _is_thread_root(
    snapshot: TeamsChannelMessageSnapshot, *, current_thread: Any
) -> bool:
    """Whether this snapshot is the message the current conversation hangs off.

    A channel-scoped read returns the thread root among the recent messages, and
    including it would hand the agent the conversation it is already in as
    background context.
    """
    return bool(
        snapshot.message_id and current_thread and snapshot.message_id == current_thread
    )


def _extract_graph_message_attachments(
    item: dict[str, Any],
) -> list[TeamsMessageAttachmentSnapshot]:
    results: list[TeamsMessageAttachmentSnapshot] = []
    for raw in item.get("attachments") or []:
        if not isinstance(raw, dict):
            continue
        content_url = str(raw.get("contentUrl") or "").strip()
        if not content_url:
            continue
        name = str(raw.get("name") or "").strip() or None
        content_type = str(raw.get("contentType") or "").strip()
        file_type = ""
        if name and "." in name:
            file_type = name.rsplit(".", 1)[-1].lower()
        elif "/" in content_type:
            file_type = content_type.split("/")[-1].lower()
        results.append(
            TeamsMessageAttachmentSnapshot(
                name=name,
                download_url=content_url,
                file_type=file_type,
                content_type=content_type,
            )
        )
    return results


def _snapshot_text(
    item: dict[str, Any], attachments: list[TeamsMessageAttachmentSnapshot]
) -> str:
    """The message body, or a stand-in naming the files when there is no body."""
    text = strip_html(payload_text(payload_section(item, "body"), "content")).strip()
    if text or not attachments:
        return text
    names = ", ".join(att.name or "file" for att in attachments)
    return f"[File shared: {names}]"


def _message_snapshot_from_graph_item(
    item: dict[str, Any],
) -> TeamsChannelMessageSnapshot | None:
    attachments = _extract_graph_message_attachments(item)
    text = _snapshot_text(item, attachments)
    if not text:
        return None

    sender = payload_section(item, "from")
    user = payload_section(sender, "user") or payload_section(sender, "application")
    return TeamsChannelMessageSnapshot(
        message_id=payload_text(item, "id") or None,
        reply_to_id=payload_text(item, "replyToId") or None,
        user_id=payload_text(user, "id") or None,
        display_name=payload_text(user, "displayName") or None,
        text=text,
        attachments=attachments,
    )


class TeamsChannelHistoryMixin:
    """The channel-history half of :class:`TeamsPlatformService`."""

    async def get_recent_channel_messages(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: TeamsGetRecentMessagesParams,
    ) -> TeamsGetRecentMessagesResult:
        try:
            target = await self._resolve_history_target(ctx)
            async with new_aiohttp_session() as session:
                url = await self._history_url(target, request=request, session=session)
                data = await self._read_graph_json(url, target.token, session=session)
        except _TeamsHistoryUnavailable as exc:
            return TeamsGetRecentMessagesResult(success=False, error=str(exc))
        except Exception:
            logger.debug(
                "agent_surfaces.service.teams_get_recent_channel_messages.propagated",
                conversation_id=ctx.deps.conversation_id,
                exc_info=True,
            )
            raise

        messages: list[TeamsChannelMessageSnapshot] = []
        for snapshot in _graph_snapshots(data):
            if request.scope != "thread" and _is_thread_root(
                snapshot, current_thread=target.current_thread
            ):
                continue
            if snapshot.author_label is None:
                snapshot.author_label = channel_author_label(
                    snapshot.display_name, snapshot.user_id
                )
            messages.append(snapshot)
            if len(messages) >= request.limit:
                break

        return TeamsGetRecentMessagesResult(
            success=True,
            message=background_channel_context_note(len(messages)),
            messages=messages,
        )

    async def _resolve_history_target(
        self, ctx: RunContext[ConversationContext]
    ) -> _ChannelHistoryTarget:
        """Everything a channel-history read needs before it opens a session."""
        if ctx.deps.surface_platform != "TEAMS":
            raise _TeamsHistoryUnavailable(
                "This tool is only available in Teams conversations."
            )

        tenant_id = tenant_id_from_credentials(self.credentials)
        if not tenant_id:
            logger.debug(
                "agent_surfaces.service.teams_get_recent_channel_messages.diagnostic"
            )
            raise _TeamsHistoryUnavailable(
                "Cannot determine Teams tenant_id from account credentials."
            )

        token = await client.get_graph_token(tenant_id)
        if not token:
            logger.debug(
                "agent_surfaces.service.teams_get_recent_channel_messages.diagnostic",
                tenant_id=tenant_id,
            )
            raise _TeamsHistoryUnavailable(
                "Could not acquire Graph API token for channel history."
            )

        teams_meta = self._teams_metadata(ctx)
        channel_id = ctx.deps.external_channel_id
        if teams_meta is None or not teams_meta.team_id or not channel_id:
            raise _TeamsHistoryUnavailable(
                "Channel history is only available for team channel conversations."
            )
        return _ChannelHistoryTarget(
            token=token,
            team_id=teams_meta.team_id,
            team_aad_group_id=teams_meta.team_aad_group_id,
            service_url=teams_meta.service_url,
            channel_id=channel_id,
            current_thread=ctx.deps.external_thread_id,
        )

    async def _history_url(
        self,
        target: _ChannelHistoryTarget,
        *,
        request: TeamsGetRecentMessagesParams,
        session: aiohttp.ClientSession,
    ) -> str:
        """The endpoint this request reads from, with ``auto`` resolved."""
        graph_team_id = await client.resolve_graph_team_id(
            raw_team_id=target.team_id,
            team_aad_group_id=target.team_aad_group_id,
            service_url=target.service_url,
            session=session,
        )
        if not graph_team_id:
            raise _TeamsHistoryUnavailable(
                "Could not resolve the Microsoft Teams team ID required for "
                "channel history."
            )
        scope = request.scope
        if scope == "auto":
            scope = "thread" if target.thread_id else "channel"
        if scope == "thread" and target.thread_id is None:
            raise _TeamsHistoryUnavailable(
                "There is no current Teams thread to inspect in this conversation."
            )
        return _channel_messages_url(
            self._graph_base,
            graph_team_id=graph_team_id,
            channel_id=target.channel_id,
            thread_id=target.thread_id if scope == "thread" else None,
            limit=request.limit,
        )

    async def _read_graph_json(
        self, url: str, token: str, *, session: aiohttp.ClientSession
    ) -> Any:
        async with session.get(url, headers=client.auth_headers(token)) as response:
            if response.status >= 400:
                await response.text()
                logger.debug(
                    "agent_surfaces.service.teams_get_recent_channel_messages.diagnostic",
                    status=response.status,
                )
                raise _TeamsHistoryUnavailable(
                    f"Graph API returned HTTP {response.status}."
                )
            return await response.json()

    async def fetch_recent_context(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """Recent channel/thread messages via Graph for background context on a
        mention. Best-effort: missing tenant/team/token or any error → empty."""
        tenant_id = tenant_id_from_credentials(self.credentials)
        channel_id = event.external_channel_id
        if not tenant_id or not channel_id:
            return []
        meta = build_surface_event_metadata("TEAMS", event.metadata or {})
        team_id = getattr(meta, "team_id", None)
        if not team_id:
            return []

        current_thread = event.external_thread_id
        target = _ChannelHistoryTarget(
            token="",
            team_id=team_id,
            team_aad_group_id=getattr(meta, "team_aad_group_id", None),
            service_url=getattr(meta, "service_url", None),
            channel_id=channel_id,
            current_thread=current_thread,
        )
        try:
            data = await self._read_context_messages(
                tenant_id=tenant_id, target=target, limit=limit
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.teams_fetch_recent_context_channel.diagnostic",
                channel_id=channel_id,
            )
            return []

        out: list[SurfaceContextMessage] = []
        for snapshot in _graph_snapshots(data):
            text = snapshot.text.strip()
            if not text:
                continue
            if target.thread_id is None and _is_thread_root(
                snapshot, current_thread=current_thread
            ):
                continue
            out.append(
                SurfaceContextMessage(
                    author=snapshot.author_label
                    or channel_author_label(snapshot.display_name, snapshot.user_id),
                    text=text,
                    ts=snapshot.message_id,
                )
            )
            if len(out) >= limit:
                break
        return out

    async def _read_context_messages(
        self, *, tenant_id: str, target: _ChannelHistoryTarget, limit: int
    ) -> Any:
        """Graph channel messages, or None when anything at all goes wrong.

        Background context is a nice-to-have on a mention: a missing token or a
        Graph error costs the agent some history, which is not worth failing the
        message over.
        """
        token = await client.get_graph_token(tenant_id)
        if not token:
            return None
        async with new_aiohttp_session() as session:
            graph_team_id = await client.resolve_graph_team_id(
                raw_team_id=target.team_id,
                team_aad_group_id=target.team_aad_group_id,
                service_url=target.service_url,
                session=session,
            )
            if not graph_team_id:
                return None
            url = _channel_messages_url(
                self._graph_base,
                graph_team_id=graph_team_id,
                channel_id=target.channel_id,
                thread_id=target.thread_id,
                limit=limit,
            )
            async with session.get(url, headers=client.auth_headers(token)) as response:
                if response.status >= 400:
                    return None
                return await response.json()
