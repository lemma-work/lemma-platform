"""Teams tool operations (file share/download, channel history) over Graph."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import SurfaceContextMessage
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TeamsSurfaceEventMetadata,
    build_surface_event_metadata,
)
from app.modules.agent_surfaces.platforms.common import (
    background_channel_context_note,
    channel_author_label,
)
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.client import GRAPH_BASE
from app.modules.agent_surfaces.platforms.teams.parser import strip_html
from app.modules.agent_surfaces.platforms.teams.models import (
    TeamsChannelMessageSnapshot,
    TeamsGetRecentMessagesParams,
    TeamsGetRecentMessagesResult,
    TeamsMessageAttachmentSnapshot,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class TeamsPlatformService:
    def __init__(self, *, credentials: dict[str, Any]) -> None:
        self.credentials = credentials
        self._graph_base = str(credentials.get("graph_api_base_url") or GRAPH_BASE).rstrip("/")

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Teams attachment (no RunContext)."""
        del event
        download_url = str(attachment.get("download_url") or "").strip()
        if not download_url:
            return None
        content_type = str(attachment.get("content_type") or "").strip()
        tenant_id = _tenant_id_from_credentials(self.credentials)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        ) as session:
            plan = await self._resolve_download_plan(
                session=session,
                tenant_id=tenant_id,
                download_url=download_url,
                content_type=content_type,
            )
            if plan is None:
                return None
            content = await self._fetch_content(
                session=session,
                url=plan["url"],
                headers=plan["headers"],
                mode=str(plan["mode"]),
            )
        if content is None:
            return None
        file_name = (
            str(attachment.get("name") or "").strip()
            or _filename_from_url(download_url)
            or "teams_file"
        )
        mime_type = (
            str(attachment.get("mime_type") or content_type or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

    async def get_recent_channel_messages(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: TeamsGetRecentMessagesParams,
    ) -> TeamsGetRecentMessagesResult:
        if ctx.deps.surface_platform != "TEAMS":
            return TeamsGetRecentMessagesResult(
                success=False,
                error="This tool is only available in Teams conversations.",
            )

        tenant_id = _tenant_id_from_credentials(self.credentials)
        if not tenant_id:
            logger.warning("Teams get_recent_channel_messages missing tenant_id")
            return TeamsGetRecentMessagesResult(
                success=False,
                error="Cannot determine Teams tenant_id from account credentials.",
            )

        token = await client.get_graph_token(tenant_id)
        if not token:
            logger.warning(
                "Teams get_recent_channel_messages could not acquire Graph token tenant=%s",
                tenant_id,
            )
            return TeamsGetRecentMessagesResult(
                success=False,
                error="Could not acquire Graph API token for channel history.",
            )

        teams_meta = self._teams_metadata(ctx)
        team_id = teams_meta.team_id if teams_meta is not None else None
        team_aad_group_id = teams_meta.team_aad_group_id if teams_meta is not None else None
        service_url = teams_meta.service_url if teams_meta is not None else None
        channel_id = ctx.deps.external_channel_id
        current_thread = ctx.deps.external_thread_id
        if not team_id or not channel_id:
            return TeamsGetRecentMessagesResult(
                success=False,
                error="Channel history is only available for team channel conversations.",
            )

        try:
            async with aiohttp.ClientSession() as session:
                graph_team_id = await client.resolve_graph_team_id(
                    raw_team_id=team_id,
                    team_aad_group_id=team_aad_group_id,
                    service_url=service_url,
                    session=session,
                )
                if not graph_team_id:
                    return TeamsGetRecentMessagesResult(
                        success=False,
                        error="Could not resolve the Microsoft Teams team ID required for channel history.",
                    )

                scope = request.scope
                if scope == "auto":
                    scope = (
                        "thread"
                        if current_thread and str(current_thread) != str(channel_id)
                        else "channel"
                    )

                if scope == "thread":
                    if not current_thread or str(current_thread) == str(channel_id):
                        return TeamsGetRecentMessagesResult(
                            success=False,
                            error="There is no current Teams thread to inspect in this conversation.",
                        )
                    url = (
                        f"{self._graph_base}/teams/{quote(str(graph_team_id))}/channels/"
                        f"{quote(str(channel_id))}/messages/{quote(str(current_thread))}/replies"
                        f"?$top={request.limit}"
                    )
                else:
                    url = (
                        f"{self._graph_base}/teams/{quote(str(graph_team_id))}/channels/"
                        f"{quote(str(channel_id))}/messages?$top={request.limit}"
                    )

                async with session.get(url, headers=client.auth_headers(token)) as response:
                    if response.status >= 400:
                        body = await response.text()
                        logger.warning(
                            "Teams get_recent_channel_messages graph failure status=%s body=%s",
                            response.status,
                            body[:300],
                        )
                        return TeamsGetRecentMessagesResult(
                            success=False,
                            error=f"Graph API returned HTTP {response.status}.",
                        )
                    data = await response.json()
        except Exception as exc:
            logger.exception(
                "Teams get_recent_channel_messages failed conversation=%s: %s",
                ctx.deps.conversation_id,
                exc,
            )
            raise

        messages: list[TeamsChannelMessageSnapshot] = []
        for item in reversed((data or {}).get("value") or []):
            if not isinstance(item, dict):
                continue
            snapshot = _message_snapshot_from_graph_item(item)
            if snapshot is None:
                continue
            if (
                request.scope != "thread"
                and snapshot.message_id
                and current_thread
                and snapshot.message_id == current_thread
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

    async def fetch_recent_context(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """Recent channel/thread messages via Graph for background context on a
        mention. Best-effort: missing tenant/team/token or any error → empty."""
        tenant_id = _tenant_id_from_credentials(self.credentials)
        channel_id = event.external_channel_id
        if not tenant_id or not channel_id:
            return []
        meta = build_surface_event_metadata("TEAMS", event.metadata or {})
        team_id = getattr(meta, "team_id", None)
        if not team_id:
            return []
        current_thread = event.external_thread_id
        scope = (
            "thread"
            if current_thread and str(current_thread) != str(channel_id)
            else "channel"
        )
        try:
            token = await client.get_graph_token(tenant_id)
            if not token:
                return []
            async with aiohttp.ClientSession() as session:
                graph_team_id = await client.resolve_graph_team_id(
                    raw_team_id=team_id,
                    team_aad_group_id=getattr(meta, "team_aad_group_id", None),
                    service_url=getattr(meta, "service_url", None),
                    session=session,
                )
                if not graph_team_id:
                    return []
                base = (
                    f"{self._graph_base}/teams/{quote(str(graph_team_id))}/channels/"
                    f"{quote(str(channel_id))}/messages"
                )
                url = (
                    f"{base}/{quote(str(current_thread))}/replies?$top={limit}"
                    if scope == "thread"
                    else f"{base}?$top={limit}"
                )
                async with session.get(
                    url, headers=client.auth_headers(token)
                ) as response:
                    if response.status >= 400:
                        return []
                    data = await response.json()
        except Exception as exc:
            logger.warning(
                "Teams fetch_recent_context failed channel=%s: %s", channel_id, exc
            )
            return []

        out: list[SurfaceContextMessage] = []
        for item in reversed((data or {}).get("value") or []):
            if not isinstance(item, dict):
                continue
            snapshot = _message_snapshot_from_graph_item(item)
            if snapshot is None or not snapshot.text.strip():
                continue
            if (
                scope != "thread"
                and snapshot.message_id
                and current_thread
                and snapshot.message_id == current_thread
            ):
                continue
            author = snapshot.author_label or channel_author_label(
                snapshot.display_name, snapshot.user_id
            )
            out.append(
                SurfaceContextMessage(
                    author=author, text=snapshot.text.strip(), ts=snapshot.message_id
                )
            )
            if len(out) >= limit:
                break
        return out

    def _teams_metadata(
        self,
        ctx: RunContext[ConversationContext],
    ) -> TeamsSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, TeamsSurfaceEventMetadata):
            return metadata
        return None

    async def _resolve_download_plan(
        self,
        *,
        session: aiohttp.ClientSession,
        tenant_id: str | None,
        download_url: str,
        content_type: str,
    ) -> dict[str, Any] | None:
        normalized_url = str(download_url).strip()
        if not normalized_url:
            return None

        if _looks_like_bot_attachment_url(normalized_url):
            bot_token = await client.get_bot_token()
            if not bot_token:
                logger.warning(
                    "Teams download plan missing bot token for bot attachment url=%s",
                    normalized_url,
                )
                return None
            return {
                "mode": "bot",
                "url": normalized_url,
                "headers": {"Authorization": f"Bearer {bot_token}"},
            }

        if not tenant_id:
            logger.warning("Teams download plan missing tenant_id for url=%s", normalized_url)
            return None

        graph_token = await client.get_graph_token(tenant_id)
        if not graph_token:
            logger.warning("Teams download plan missing graph token tenant=%s", tenant_id)
            return None

        if _is_sharepoint_url(normalized_url):
            # Resolve SharePoint browser/share URLs via Graph shares first.
            shared_item = await _resolve_shared_item_content_request(
                session=session,
                token=graph_token,
                url=normalized_url,
            )
            if shared_item:
                return shared_item

            if _is_raw_sharepoint_document_url(normalized_url):
                content_url = await _resolve_sharepoint_file_content_url(
                    session=session,
                    token=graph_token,
                    url=normalized_url,
                )
                if content_url:
                    return {
                        "mode": "graph",
                        "url": content_url,
                        "headers": {"Authorization": f"Bearer {graph_token}"},
                    }

            logger.warning(
                "Teams download plan could not resolve SharePoint url=%s",
                normalized_url,
            )
            return None

        shared_item = await _resolve_shared_item_content_request(
            session=session,
            token=graph_token,
            url=normalized_url,
        )
        if shared_item:
            return shared_item

        if content_type.startswith("image/"):
            logger.warning(
                "Teams download plan could not resolve image attachment url=%s",
                normalized_url,
            )
        return None

    async def _fetch_content(
        self,
        *,
        session: aiohttp.ClientSession,
        url: str,
        headers: dict[str, str],
        mode: str,
    ) -> bytes | None:
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            if response.status >= 400:
                body = await response.text()
                logger.warning(
                    "Teams download_file %s fetch failed status=%s url=%s body=%s",
                    mode,
                    response.status,
                    url[:120],
                    body[:300],
                )
                return None
            return await response.read()


def _tenant_id_from_credentials(credentials: dict[str, Any]) -> str | None:
    user_data = credentials.get("user_data") or {}
    raw = credentials.get("raw_response") or {}
    return (
        user_data.get("tenant_id")
        or user_data.get("tid")
        or raw.get("tenant_id")
        or raw.get("tid")
        or credentials.get("tenant_id")
        or credentials.get("tid")
    ) or None


def _filename_from_url(url: str) -> str | None:
    candidate = Path(str(url).split("?")[0]).name.strip()
    return candidate or None


def _is_raw_sharepoint_document_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if "sharepoint.com" not in hostname:
        return False
    return "/shared documents/" in path.lower() or "/sites/" in path.lower()


def _is_sharepoint_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return "sharepoint.com" in hostname


def _looks_like_bot_attachment_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return "trafficmanager.net" in hostname or "/v3/attachments/" in (parsed.path or "")


def _encode_share_url(url: str) -> str:
    encoded = base64.b64encode(url.encode("utf-8")).decode("utf-8")
    return "u!" + encoded.rstrip("=").replace("/", "_").replace("+", "-")


async def _resolve_shared_item_content_request(
    *,
    session: aiohttp.ClientSession,
    token: str,
    url: str,
) -> dict[str, Any] | None:
    share_token = _encode_share_url(url)
    endpoint = f"{GRAPH_BASE}/shares/{quote(share_token)}/driveItem"
    headers = client.auth_headers(token)
    headers["Prefer"] = "redeemSharingLinkIfNecessary"
    async with session.get(endpoint, headers=headers) as response:
        if response.status >= 400:
            body = await response.text()
            logger.warning(
                "Teams download_file could not resolve shared item url=%s status=%s body=%s",
                url[:120],
                response.status,
                body[:300],
            )
            return None
        data = await response.json()

    direct_url = data.get("@microsoft.graph.downloadUrl")
    if direct_url:
        return {"mode": "direct", "url": str(direct_url), "headers": {}}

    item_id = data.get("id")
    drive_id = (data.get("parentReference") or {}).get("driveId")
    if item_id and drive_id:
        return {
            "mode": "graph",
            "url": f"{GRAPH_BASE}/drives/{quote(str(drive_id))}/items/{quote(str(item_id))}/content",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    return None


async def _resolve_sharepoint_file_content_url(
    *,
    session: aiohttp.ClientSession,
    token: str,
    url: str,
) -> str | None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip()
    raw_path = parsed.path or ""
    if not hostname or not raw_path or "sharepoint.com" not in hostname:
        return None

    site_path, item_path = _split_sharepoint_site_and_item_path(raw_path)
    if not item_path:
        return None

    site_id = await _resolve_sharepoint_site_id(
        session=session,
        token=token,
        hostname=hostname,
        site_path=site_path,
    )
    if not site_id:
        return None

    return (
        f"{GRAPH_BASE}/sites/{quote(site_id)}/drive/root:"
        f"{quote(item_path, safe='/')}:/content"
    )


def _split_sharepoint_site_and_item_path(raw_path: str) -> tuple[str, str | None]:
    path = "/" + str(raw_path).lstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return "/", None
    if segments[0] in {"sites", "teams", "personal"} and len(segments) >= 3:
        return "/" + "/".join(segments[:2]), "/" + "/".join(segments[2:])
    if len(segments) >= 2:
        return "/", "/" + "/".join(segments)
    return "/", None


async def _resolve_sharepoint_site_id(
    *,
    session: aiohttp.ClientSession,
    token: str,
    hostname: str,
    site_path: str,
) -> str | None:
    endpoint = (
        f"{GRAPH_BASE}/sites/root"
        if site_path == "/"
        else f"{GRAPH_BASE}/sites/{hostname}:{quote(site_path, safe='/')}"
    )
    async with session.get(endpoint, headers=client.auth_headers(token)) as response:
        if response.status >= 400:
            body = await response.text()
            logger.warning(
                "Teams download_file could not resolve SharePoint site hostname=%s path=%s status=%s body=%s",
                hostname,
                site_path,
                response.status,
                body[:300],
            )
            return None
        data = await response.json()
    site_id = data.get("id")
    return str(site_id) if site_id else None


def _extract_graph_message_attachments(item: dict[str, Any]) -> list[TeamsMessageAttachmentSnapshot]:
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


def _message_snapshot_from_graph_item(
    item: dict[str, Any],
) -> TeamsChannelMessageSnapshot | None:
    body = item.get("body") or {}
    text = strip_html(str(body.get("content") or "")).strip()
    attachments = _extract_graph_message_attachments(item)
    if not text and attachments:
        names = ", ".join(att.name or "file" for att in attachments)
        text = f"[File shared: {names}]"
    if not text:
        return None

    sender = item.get("from") or {}
    user = sender.get("user") or sender.get("application") or {}
    return TeamsChannelMessageSnapshot(
        message_id=str(item.get("id") or "") or None,
        reply_to_id=str(item.get("replyToId") or "") or None,
        user_id=str(user.get("id") or "") or None,
        display_name=str(user.get("displayName") or "") or None,
        text=text,
        attachments=attachments,
    )
