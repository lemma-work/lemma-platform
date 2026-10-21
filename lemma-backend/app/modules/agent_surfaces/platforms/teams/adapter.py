"""Teams surface adapter: inbound parsing/enrichment and outbound replies."""

from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    TeamsReplyTarget,
    reply_target_as,
)

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from typing import Any
from urllib.parse import quote


from app.core.log.log import get_logger
import aiohttp

from app.core.net.aiohttp_client import new_aiohttp_session
from app.modules.agent_surfaces.domain.errors import AgentSurfaceValidationError
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.place_privacy import PlaceFacts, PlacePrivacy
from app.modules.agent_surfaces.domain.groups import ParsedGroupUpdate
from app.modules.agent_surfaces.domain.line_changes import ParsedLineChange
from app.modules.agent_surfaces.platforms.teams.line_changes import teams_line_changes
from app.modules.agent_surfaces.platforms.teams.group_updates import (
    teams_group_updates,
)
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.adapter_egress import (
    TeamsSurfaceEgress,
)
from app.modules.agent_surfaces.platforms.teams.client import GRAPH_BASE
from app.modules.agent_surfaces.platforms.teams.parser import (
    TeamsMessageParser,
    extract_graph_message_attachments,
)

logger = get_logger(__name__)


def _graph_message_url(
    *,
    graph_team_id: Any,
    channel_id: Any,
    message_id: Any,
    root_thread_id: Any,
) -> str:
    """The Graph URL for one message: a reply inside a thread, or a channel post."""
    base = (
        f"{GRAPH_BASE}/teams/{quote(str(graph_team_id))}"
        f"/channels/{quote(str(channel_id))}/messages"
    )
    if root_thread_id and root_thread_id != message_id:
        return f"{base}/{quote(str(root_thread_id))}/replies/{quote(str(message_id))}"
    return f"{base}/{quote(str(message_id))}"


def tenant_verified_address(user_principal_name: object) -> str | None:
    """The sender's address, but only one their tenant proved it owns.

    A Teams sender's address resolves to a Lemma user, and the run then
    executes as that user. Entra's `mail` attribute is free text an
    administrator (or a guest invitation) can set to anyone's address, so it
    is never used. A user principal name's suffix has to be a domain the
    tenant verified in DNS, so it cannot claim somebody else's -- except for a
    guest, whose principal name is the host tenant's rewrite of a foreign
    address (`alice_example.com#EXT#@host.onmicrosoft.com`) and proves nothing
    about it. A sender with no such address goes through first-use
    verification instead of being matched by email.
    """
    principal = str(user_principal_name or "").strip()
    if "@" not in principal or "#ext#" in principal.lower():
        return None
    return principal


class TeamsSurfaceAdapter(TeamsSurfaceEgress):
    platform = "TEAMS"

    def __init__(self) -> None:
        self._parser = TeamsMessageParser()

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self._parser.parse(payload, headers)

    async def enrich_inbound_event(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent:
        del credentials
        item = await self._graph_message_for(event)
        if item is None:
            return event

        graph_attachments = extract_graph_message_attachments(item)
        if not graph_attachments:
            logger.debug(
                "agent_surfaces.adapter.teams_inbound_event_enrichment_found.diagnostic"
            )
            return event

        enriched = event.model_copy(deep=True)
        enriched.message_text = self._text_with_attachments(event, graph_attachments)
        enriched.metadata = {**event.metadata, "attachments": graph_attachments}
        logger.debug(
            "agent_surfaces.adapter.teams_inbound_event_enriched_graph.observed",
            count=len(graph_attachments),
        )
        return enriched

    async def _graph_message_for(
        self, event: ParsedInboundSurfaceEvent
    ) -> dict[str, Any] | None:
        """The Graph item backing this message, or None when it cannot be fetched.

        Every branch is a reason to leave the event exactly as it arrived, which
        is why they all read alike: say why, hand back nothing.
        """
        attachments = event.metadata.get("attachments") or []
        if attachments:
            logger.debug(
                "agent_surfaces.adapter.teams_inbound_event_already_includes.observed",
                count=len(attachments),
            )
            return None

        if event.is_dm:
            logger.debug("agent_surfaces.adapter.teams_inbound_dm_event_has.diagnostic")
            return None

        tenant_id = event.tenant_id
        team_id = reply_target_as(event, TeamsReplyTarget).team_id
        channel_id = reply_target_as(event, TeamsReplyTarget).channel_id
        message_id = event.external_message_id
        if not tenant_id or not team_id or not channel_id or not message_id:
            logger.debug(
                "agent_surfaces.adapter.teams_inbound_event_cannot_be.diagnostic",
                tenant_id=tenant_id,
                team_id=team_id,
                channel_id=channel_id,
            )
            return None

        token = await self._get_graph_token(tenant_id)
        if not token:
            logger.debug(
                "agent_surfaces.adapter.teams_inbound_event_enrichment_skipped.diagnostic",
                tenant_id=tenant_id,
            )
            return None

        graph_team_id = await client.resolve_graph_team_id(
            raw_team_id=str(team_id),
            team_aad_group_id=reply_target_as(
                event, TeamsReplyTarget
            ).team_aad_group_id,
            service_url=reply_target_as(event, TeamsReplyTarget).service_url,
        )
        if not graph_team_id:
            logger.debug(
                "agent_surfaces.adapter.teams_inbound_event_enrichment_skipped.diagnostic",
                team_id=team_id,
            )
            return None

        item = await client.get_json(
            _graph_message_url(
                graph_team_id=graph_team_id,
                channel_id=channel_id,
                message_id=message_id,
                root_thread_id=(
                    event.external_thread_id
                    if event.metadata.get("is_thread_reply")
                    else None
                ),
            ),
            token,
        )
        if isinstance(item, dict):
            return item
        logger.debug(
            "agent_surfaces.adapter.teams_inbound_event_enrichment_could.diagnostic"
        )
        return None

    def parse_group_updates(
        self, payload: dict[str, object]
    ) -> list[ParsedGroupUpdate]:
        return teams_group_updates(payload)

    def parse_line_changes(self, payload: dict[str, object]) -> list[ParsedLineChange]:
        return teams_line_changes(payload, self._parser)

    async def read_place_privacy(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> PlaceFacts | None:
        """A group chat is its members'; a team channel is what Graph says its
        ``membershipType`` is. None when Graph cannot be asked."""
        del credentials
        if str(event.metadata.get("channel_type") or "").lower() == "groupchat":
            return PlaceFacts(privacy=PlacePrivacy.PRIVATE)
        target = reply_target_as(event, TeamsReplyTarget)
        if not event.tenant_id or not target.team_id or not target.channel_id:
            return None
        token = await self._get_graph_token(event.tenant_id)
        if not token:
            return None
        try:
            graph_team_id = await client.resolve_graph_team_id(
                raw_team_id=str(target.team_id),
                team_aad_group_id=target.team_aad_group_id,
                service_url=target.service_url,
            )
            if not graph_team_id:
                return None
            channel = await client.get_json(
                f"{client.GRAPH_BASE}/teams/{quote(graph_team_id)}"
                f"/channels/{quote(str(target.channel_id))}?$select=membershipType",
                token,
            )
        except aiohttp.ClientError, TimeoutError:
            logger.info("agent_surfaces.teams.place_privacy_unknown.observed")
            return None
        membership = str((channel or {}).get("membershipType") or "").lower()
        if membership == "standard":
            return PlaceFacts(privacy=PlacePrivacy.PUBLIC)
        if membership in ("private", "shared"):
            return PlaceFacts(privacy=PlacePrivacy.PRIVATE)
        return None

    def _text_with_attachments(
        self, event: ParsedInboundSurfaceEvent, attachments: list[dict[str, Any]]
    ) -> str:
        """The message text with the attachment block appended, at most once."""
        attachment_text = self._parser.attachment_prompt_text(attachments)
        text = event.message_text.strip()
        if not attachment_text or attachment_text in text:
            return text
        return f"{text}\n\n{attachment_text}" if text else attachment_text

    async def fetch_sender_profile(
        self, *, credentials: SurfaceCredentials, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        del credentials
        tenant_id = event.tenant_id
        # Prefer the AAD Object ID — it's a stable UUID that Graph accepts directly.
        # Fall back to the Bot Framework user ID (29:xxx).
        aad_id = event.sender_aad_object_id
        bf_user_id = event.sender_external_user_id

        if not tenant_id:
            logger.debug(
                "agent_surfaces.adapter.teams_fetch_sender_profile_missing.diagnostic"
            )
            return None
        if not aad_id and not bf_user_id:
            logger.debug(
                "agent_surfaces.adapter.teams_fetch_sender_profile_missing.diagnostic"
            )
            return None

        # ── Strategy 1: Microsoft Graph (requires admin consent for User.Read.All) ──
        identifier = aad_id or bf_user_id
        graph_token = await self._get_graph_token(tenant_id)
        if graph_token:
            url = (
                f"{GRAPH_BASE}/users/{quote(str(identifier))}"
                "?$select=id,displayName,mail,userPrincipalName,mobilePhone"
            )
            async with new_aiohttp_session() as session:
                async with session.get(
                    url, headers=client.auth_headers(graph_token)
                ) as response:
                    if response.status < 400:
                        data = await response.json()
                        email = tenant_verified_address(data.get("userPrincipalName"))
                        return SurfaceSenderProfile(
                            external_user_id=str(data.get("id") or bf_user_id or ""),
                            email=email,
                            phone=data.get("mobilePhone"),
                            display_name=data.get("displayName")
                            or event.sender_display_name,
                            raw_profile=data,
                        )
                    await response.text()
                    logger.debug(
                        "agent_surfaces.adapter.teams_fetch_sender_profile_graph.diagnostic",
                        status=response.status,
                        tenant_id=tenant_id,
                    )
        else:
            logger.debug(
                "agent_surfaces.adapter.teams_fetch_sender_profile_could.diagnostic",
                tenant_id=tenant_id,
            )

        # ── Strategy 2: Bot Framework Connector getConversationMember ──
        # Does NOT require admin consent — uses the bot's own token.
        # May expose email via 'properties' in some tenant configurations.
        if bf_user_id:
            email = await self._fetch_email_from_bf_connector(event, bf_user_id)
            if email:
                return SurfaceSenderProfile(
                    external_user_id=str(aad_id or bf_user_id),
                    email=email,
                    display_name=event.sender_display_name,
                )

        # Return partial profile (no email) so at least display_name is captured.
        logger.debug(
            "agent_surfaces.adapter.teams_fetch_sender_profile_could.diagnostic",
            tenant_id=tenant_id,
        )
        return SurfaceSenderProfile(
            external_user_id=str(aad_id or bf_user_id or ""),
            display_name=event.sender_display_name,
        )

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self._parser.parse_interaction(payload, headers)

    async def fetch_thread_context(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ):
        from app.modules.agent_surfaces.platforms.teams.service import (
            TeamsPlatformService,
        )

        return await TeamsPlatformService(credentials=credentials).fetch_recent_context(
            event=event, limit=limit
        )

    async def download_attachment(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        from app.modules.agent_surfaces.platforms.teams.service import (
            TeamsPlatformService,
        )

        return await TeamsPlatformService(
            credentials=credentials
        ).download_attachment_bytes(event, attachment)

    # Seams kept as methods so tests can stub token acquisition per adapter.
    async def _get_graph_token(self, tenant_id: str) -> str | None:
        return await client.get_graph_token(tenant_id)

    async def _get_bot_token(self, tenant_id: str | None = None) -> str | None:
        del tenant_id
        return await client.get_bot_token()

    async def _fetch_email_from_bf_connector(
        self,
        event: ParsedInboundSurfaceEvent,
        bf_user_id: str,
    ) -> str | None:
        """Try to get user email via Bot Framework Connector getConversationMember.

        The response `properties` dict sometimes contains `email` depending on
        the tenant's Teams configuration. This does not require Graph admin consent.
        """
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not event.tenant_id or not conversation_id:
            return None
        try:
            base_url = client.bf_service_url(
                reply_target_as(event, TeamsReplyTarget).service_url
            )
        except AgentSurfaceValidationError:
            logger.warning(
                "agent_surfaces.adapter.teams_service_url_refused.degraded",
                exc_info=True,
            )
            return None
        bot_token = await self._get_bot_token(event.tenant_id)
        if not bot_token:
            return None

        url = (
            f"{base_url}"
            f"/v3/conversations/{quote(str(conversation_id))}/members/{quote(bf_user_id)}"
        )
        try:
            async with new_aiohttp_session() as session:
                async with session.get(
                    url, headers=client.auth_headers(bot_token)
                ) as response:
                    if response.status >= 400:
                        return None
                    data = await response.json()
        # `ValueError` too: `response.json()` decodes the body, and a
        # non-JSON 200 from the Bot Framework connector is a `JSONDecodeError`,
        # which is not an `aiohttp.ClientError`.
        except aiohttp.ClientError, TimeoutError, ValueError:
            logger.debug(
                "agent_surfaces.adapter.teams_fetch_email_bf_connector.observed"
            )
            return None

        # The roster's `email` is the directory's `mail`, which an administrator
        # sets freely; only the principal name is bound to a verified domain.
        props = data.get("properties")
        principal = (
            props.get("userPrincipalName") if isinstance(props, dict) else None
        ) or data.get("userPrincipalName")
        return tenant_verified_address(principal)
