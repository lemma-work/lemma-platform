"""Teams tool operations (file share/download, channel history) over Graph."""

from __future__ import annotations

import mimetypes
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import aiohttp
from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TeamsSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.channel_history import (
    TeamsChannelHistoryMixin,
)
from app.modules.agent_surfaces.platforms.teams.credentials import (
    tenant_id_from_credentials,
)
from app.modules.agent_surfaces.platforms.teams.attachment_urls import (
    encode_share_url,
    filename_from_url,
    hostname_of,
    is_raw_sharepoint_document_url,
    is_sharepoint_url,
    split_sharepoint_site_and_item_path,
    looks_like_bot_attachment_url,
)
from app.modules.agent_surfaces.platforms.teams.client import GRAPH_BASE
from app.core.log.log import get_logger
from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)

logger = get_logger(__name__)
# Graph answers `/content` with a redirect to a pre-signed URL, so redirects have
# to be followed -- but never with the credential still attached.
_MAX_DOWNLOAD_REDIRECTS = 5


class TeamsPlatformService(TeamsChannelHistoryMixin):
    def __init__(self, *, credentials: dict[str, Any]) -> None:
        self.credentials = credentials
        self._graph_base = str(
            credentials.get("graph_api_base_url") or GRAPH_BASE
        ).rstrip("/")
        # Deployments that reach Bot Framework through a non-public host (the
        # test doubles, an on-prem gateway) declare it here. It joins the hosts
        # allowed to receive the bot token; it does not replace them.
        self._bot_service_host = hostname_of(
            str(credentials.get("bot_service_base_url") or "")
        )

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
        tenant_id = tenant_id_from_credentials(self.credentials)
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
            or filename_from_url(download_url)
            or "teams_file"
        )
        mime_type = (
            str(attachment.get("mime_type") or content_type or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

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

        if looks_like_bot_attachment_url(
            normalized_url, extra_host=self._bot_service_host
        ):
            bot_token = await client.get_bot_token()
            if not bot_token:
                logger.debug(
                    "agent_surfaces.service.teams_download_plan_missing_bot.diagnostic"
                )
                return None
            return {
                "mode": "bot",
                "url": normalized_url,
                "headers": {"Authorization": f"Bearer {bot_token}"},
            }

        if not tenant_id:
            logger.debug(
                "agent_surfaces.service.teams_download_plan_missing_tenant.diagnostic"
            )
            return None

        graph_token = await client.get_graph_token(tenant_id)
        if not graph_token:
            logger.debug(
                "agent_surfaces.service.teams_download_plan_missing_graph.diagnostic",
                tenant_id=tenant_id,
            )
            return None

        if is_sharepoint_url(normalized_url):
            # Resolve SharePoint browser/share URLs via Graph shares first.
            shared_item = await _resolve_shared_item_content_request(
                session=session,
                token=graph_token,
                url=normalized_url,
            )
            if shared_item:
                return shared_item

            if is_raw_sharepoint_document_url(normalized_url):
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

            logger.debug(
                "agent_surfaces.service.teams_download_plan_could_not.diagnostic"
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
            logger.debug(
                "agent_surfaces.service.teams_download_plan_could_not.diagnostic"
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
        """Fetch the planned URL, dropping the credential across redirects.

        Graph redirects `/content` to a pre-signed URL that needs no header of
        ours, so following by hand and clearing the headers is both what
        Microsoft expects and what stops a redirect to an unvetted host from
        collecting the token.
        """
        current_url = url
        current_headers = headers
        for _ in range(_MAX_DOWNLOAD_REDIRECTS + 1):
            async with session.get(
                current_url, headers=current_headers, allow_redirects=False
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    current_url = urljoin(current_url, location)
                    current_headers = {}
                    continue
                if response.status >= 400:
                    await response.text()
                    logger.debug(
                        "agent_surfaces.service.teams_download_file_s_fetch.diagnostic",
                        status=response.status,
                    )
                    return None
                # aiohttp's chunk iterator, same cap as the httpx platforms.
                chunks = response.content.iter_chunked(64 * 1024)
                return await read_capped(chunks, max_bytes=INBOUND_ATTACHMENT_BYTE_CAP)
        logger.debug("agent_surfaces.service.teams_download_file_redirects.diagnostic")
        return None


async def _resolve_shared_item_content_request(
    *,
    session: aiohttp.ClientSession,
    token: str,
    url: str,
) -> dict[str, Any] | None:
    share_token = encode_share_url(url)
    endpoint = f"{GRAPH_BASE}/shares/{quote(share_token)}/driveItem"
    headers = client.auth_headers(token)
    headers["Prefer"] = "redeemSharingLinkIfNecessary"
    async with session.get(endpoint, headers=headers) as response:
        if response.status >= 400:
            await response.text()
            logger.debug(
                "agent_surfaces.service.teams_download_file_could_not.diagnostic",
                status=response.status,
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
    # A substring check accepts `sharepoint.com.example.net`, a host anyone can
    # register. `is_sharepoint_url` asks for the domain or a real subdomain.
    if not raw_path or not is_sharepoint_url(url):
        return None

    site_path, item_path = split_sharepoint_site_and_item_path(raw_path)
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
            await response.text()
            logger.debug(
                "agent_surfaces.service.teams_download_file_could_not.diagnostic",
                status=response.status,
            )
            return None
        data = await response.json()
    site_id = data.get("id")
    return str(site_id) if site_id else None
