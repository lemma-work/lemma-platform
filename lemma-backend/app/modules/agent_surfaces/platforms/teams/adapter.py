"""Teams surface adapter: inbound parsing/enrichment and outbound replies."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp

from app.modules.agent_surfaces.config import surface_settings
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.client import GRAPH_BASE
from app.modules.agent_surfaces.platforms.teams.parser import (
    TEAMS_FORM_CALLBACK_KEY,
    TeamsMessageParser,
    extract_graph_message_attachments,
)

logger = get_logger(__name__)


class TeamsSurfaceAdapter(BaseSurfaceAdapter):
    platform = "TEAMS"

    def __init__(self) -> None:
        self._parser = TeamsMessageParser()

    async def parse_inbound_event(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedInboundSurfaceEvent | None:
        return self._parser.parse(payload, headers)

    async def enrich_inbound_event(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> ParsedInboundSurfaceEvent:
        del credentials
        attachments = event.metadata.get("attachments") or []
        if attachments:
            logger.info(
                "Teams inbound event already includes %d attachment(s) message_id=%s",
                len(attachments),
                event.external_message_id,
            )
            return event

        if event.is_dm:
            logger.warning(
                "Teams inbound DM event has no attachment metadata message_id=%s conversation_id=%s",
                event.external_message_id,
                event.reply_target.get("conversation_id"),
            )
            return event

        tenant_id = event.tenant_id
        team_id = event.reply_target.get("team_id")
        channel_id = event.reply_target.get("channel_id")
        message_id = event.external_message_id
        if not tenant_id or not team_id or not channel_id or not message_id:
            logger.warning(
                "Teams inbound event cannot be enriched due to missing graph identifiers tenant=%s team=%s channel=%s message_id=%s",
                tenant_id,
                team_id,
                channel_id,
                message_id,
            )
            return event

        token = await self._get_graph_token(tenant_id)
        if not token:
            logger.warning(
                "Teams inbound event enrichment skipped because Graph token could not be acquired tenant=%s message_id=%s",
                tenant_id,
                message_id,
            )
            return event

        graph_team_id = await client.resolve_graph_team_id(
            raw_team_id=str(team_id),
            team_aad_group_id=event.reply_target.get("team_aad_group_id"),
            service_url=event.reply_target.get("service_url"),
        )
        if not graph_team_id:
            logger.warning(
                "Teams inbound event enrichment skipped because Graph team id could not be resolved raw_team=%s message_id=%s",
                team_id,
                message_id,
            )
            return event

        is_thread_reply = bool(event.metadata.get("is_thread_reply"))
        root_thread_id = event.external_thread_id
        if is_thread_reply and root_thread_id and root_thread_id != message_id:
            url = (
                f"{GRAPH_BASE}/teams/{quote(str(graph_team_id))}/channels/"
                f"{quote(str(channel_id))}/messages/{quote(str(root_thread_id))}/replies/"
                f"{quote(str(message_id))}"
            )
        else:
            url = (
                f"{GRAPH_BASE}/teams/{quote(str(graph_team_id))}/channels/"
                f"{quote(str(channel_id))}/messages/{quote(str(message_id))}"
            )

        item = await client.get_json(url, token)
        if not isinstance(item, dict):
            logger.warning(
                "Teams inbound event enrichment could not load current message from Graph message_id=%s url=%s",
                message_id,
                url,
            )
            return event

        graph_attachments = extract_graph_message_attachments(item)
        if not graph_attachments:
            logger.warning(
                "Teams inbound event enrichment found no attachments in Graph message_id=%s",
                message_id,
            )
            return event

        attachment_text = self._parser.attachment_prompt_text(graph_attachments)
        text = event.message_text.strip()
        if attachment_text and attachment_text not in text:
            text = f"{text}\n\n{attachment_text}" if text else attachment_text

        enriched = event.model_copy(deep=True)
        enriched.message_text = text
        enriched.metadata = {**event.metadata, "attachments": graph_attachments}
        logger.info(
            "Teams inbound event enriched from Graph with %d attachment(s) message_id=%s",
            len(graph_attachments),
            message_id,
        )
        return enriched

    async def fetch_sender_profile(
        self, *, credentials: dict[str, Any], event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        del credentials
        tenant_id = event.tenant_id
        # Prefer the AAD Object ID — it's a stable UUID that Graph accepts directly.
        # Fall back to the Bot Framework user ID (29:xxx).
        aad_id = event.sender_aad_object_id
        bf_user_id = event.sender_external_user_id

        if not tenant_id:
            logger.warning("Teams fetch_sender_profile: missing tenant_id in event")
            return None
        if not aad_id and not bf_user_id:
            logger.warning(
                "Teams fetch_sender_profile: missing both aad_object_id and external_user_id"
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
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=client.auth_headers(graph_token)
                ) as response:
                    if response.status < 400:
                        data = await response.json()
                        email = data.get("mail") or data.get("userPrincipalName")
                        logger.info(
                            "Teams fetch_sender_profile (Graph): resolved user %s → email=%s display_name=%s",
                            identifier,
                            email,
                            data.get("displayName"),
                        )
                        return SurfaceSenderProfile(
                            external_user_id=str(data.get("id") or bf_user_id or ""),
                            email=email,
                            phone=data.get("mobilePhone"),
                            display_name=data.get("displayName")
                            or event.sender_display_name,
                            raw_profile=data,
                        )
                    body = await response.text()
                    logger.warning(
                        "Teams fetch_sender_profile: Graph API returned %s for user %s "
                        "in tenant %s — response: %s",
                        response.status,
                        identifier,
                        tenant_id,
                        body[:500],
                    )
        else:
            logger.warning(
                "Teams fetch_sender_profile: could not acquire Graph token for tenant=%s — "
                "tenant admin must grant admin consent at: "
                "https://login.microsoftonline.com/%s/adminconsent?client_id=%s",
                tenant_id,
                tenant_id,
                surface_settings.microsoft_bot_app_id or "<MICROSOFT_BOT_APP_ID>",
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
        logger.warning(
            "Teams fetch_sender_profile: could not resolve email for user %s in tenant %s — "
            "identity resolution will fall back to phone or return unresolved.",
            aad_id or bf_user_id,
            tenant_id,
        )
        return SurfaceSenderProfile(
            external_user_id=str(aad_id or bf_user_id or ""),
            display_name=event.sender_display_name,
        )

    async def send_message(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a reply via the Bot Framework Connector API.

        This uses Lemma's own bot credentials (from settings) together with the
        customer's tenant_id to acquire a Bot Framework token, then posts the
        reply to the correct conversation via the serviceUrl captured from the
        original incoming activity.
        """
        del credentials, metadata
        tenant_id = event.tenant_id
        if not tenant_id:
            return

        token = await self._get_bot_token(tenant_id)
        if not token:
            return

        conversation_id = event.reply_target.get("conversation_id")
        reply_to_id = event.reply_target.get("reply_to_id")
        if not conversation_id:
            return

        url = (
            f"{client.bf_service_url(event.reply_target.get('service_url'))}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        body: dict[str, Any] = {
            "type": "message",
            "text": message,
            # Teams only renders Markdown when the Bot Framework activity
            # explicitly declares Markdown text.
            "textFormat": "markdown",
        }
        if reply_to_id:
            body["replyToId"] = reply_to_id

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(
                url,
                headers=client.auth_headers(token),
                json=body,
            ) as response:
                response.raise_for_status()

    async def send_display_resource(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del credentials, metadata
        tenant_id = event.tenant_id
        if not tenant_id:
            return

        token = await self._get_bot_token(tenant_id)
        if not token:
            return

        conversation_id = event.reply_target.get("conversation_id")
        reply_to_id = event.reply_target.get("reply_to_id")
        if not conversation_id:
            return

        url = (
            f"{client.bf_service_url(event.reply_target.get('service_url'))}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        # The adaptive card carries the title/summary and the "Open file" button,
        # so no inline ``text`` (which would dump the raw URL next to the card).
        # ``summary`` is the Bot Framework notification field — shown in toasts,
        # not as a message bubble — so the deep link is never rendered inline.
        body: dict[str, Any] = {
            "type": "message",
            "summary": render_plan.to_caption(),
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": _teams_display_resource_card(render_plan),
                }
            ],
        }
        if reply_to_id:
            body["replyToId"] = reply_to_id

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(
                url,
                headers=client.auth_headers(token),
                json=body,
            ) as response:
                response.raise_for_status()

    async def send_questions(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del credentials, metadata
        tenant_id = event.tenant_id
        conversation_id = event.reply_target.get("conversation_id")
        if not tenant_id or not conversation_id:
            return False
        token = await self._get_bot_token(tenant_id)
        if not token:
            return False

        url = (
            f"{client.bf_service_url(event.reply_target.get('service_url'))}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        body: dict[str, Any] = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": _teams_question_card(question_plan),
                }
            ],
        }
        reply_to_id = event.reply_target.get("reply_to_id")
        if reply_to_id:
            body["replyToId"] = reply_to_id

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            async with session.post(
                url,
                headers=client.auth_headers(token),
                json=body,
            ) as response:
                response.raise_for_status()
        return True

    async def parse_inbound_interaction(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> ParsedSurfaceInteraction | None:
        return self._parser.parse_interaction(payload, headers)

    async def fetch_thread_context(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ):
        from app.modules.agent_surfaces.platforms.teams.service import (
            TeamsPlatformService,
        )

        return await TeamsPlatformService(credentials=credentials).fetch_recent_context(
            event=event, limit=limit
        )

    async def add_processing_indicator(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a Bot Framework typing indicator (animated dots) to signal the
        agent is working. Works in both DMs and channel threads.

        Note: Microsoft Graph does not support adding emoji reactions via
        application (app-only) permissions — neither for channels nor chats.
        The typing indicator is the correct Teams UX equivalent of Slack's 👀 reaction.
        """
        del credentials, metadata
        tenant_id = event.tenant_id
        if not tenant_id:
            return

        bot_token = await self._get_bot_token(tenant_id)
        if not bot_token:
            return

        conversation_id = event.reply_target.get("conversation_id")
        if not conversation_id:
            return

        url = (
            f"{client.bf_service_url(event.reply_target.get('service_url'))}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=client.auth_headers(bot_token),
                    json={"type": "typing"},
                ):
                    pass  # best-effort, ignore status
        except Exception:
            pass

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        del credentials
        tenant_id = event.tenant_id
        conversation_id = event.reply_target.get("conversation_id")
        if not tenant_id or not conversation_id:
            return progress_handle
        bot_token = await self._get_bot_token(tenant_id)
        if not bot_token:
            return progress_handle
        base = client.bf_service_url(event.reply_target.get("service_url"))
        activity_id = (progress_handle or {}).get("activity_id")
        body = {"type": "message", "text": progress_text}
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            if activity_id:
                url = (
                    f"{base}/v3/conversations/{quote(str(conversation_id))}"
                    f"/activities/{quote(str(activity_id))}"
                )
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.put(
                        url, headers=client.auth_headers(bot_token), json=body
                    ) as response:
                        response.raise_for_status()
                return progress_handle
            url = (
                f"{base}/v3/conversations/{quote(str(conversation_id))}/activities"
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, headers=client.auth_headers(bot_token), json=body
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
            new_id = (data or {}).get("id")
            return {"activity_id": new_id} if new_id else progress_handle
        except Exception:
            return progress_handle

    async def end_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Delete the streamed progress ("thinking") activity at run end so it
        does not linger next to the final answer (the final answer is delivered
        separately). Mirrors Slack/Telegram end_progress. Best-effort."""
        del credentials
        activity_id = (progress_handle or {}).get("activity_id")
        tenant_id = event.tenant_id
        conversation_id = event.reply_target.get("conversation_id")
        if not activity_id or not tenant_id or not conversation_id:
            return
        bot_token = await self._get_bot_token(tenant_id)
        if not bot_token:
            return
        base = client.bf_service_url(event.reply_target.get("service_url"))
        url = (
            f"{base}/v3/conversations/{quote(str(conversation_id))}"
            f"/activities/{quote(str(activity_id))}"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.delete(
                    url, headers=client.auth_headers(bot_token)
                ) as response:
                    response.raise_for_status()
        except Exception:
            return

    async def download_attachment(
        self,
        *,
        credentials: dict[str, Any],
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
        if not event.tenant_id:
            return None
        bot_token = await self._get_bot_token(event.tenant_id)
        if not bot_token:
            return None

        conversation_id = event.reply_target.get("conversation_id")
        if not conversation_id:
            return None

        url = (
            f"{client.bf_service_url(event.reply_target.get('service_url'))}"
            f"/v3/conversations/{quote(str(conversation_id))}/members/{quote(bf_user_id)}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=client.auth_headers(bot_token)
                ) as response:
                    if response.status >= 400:
                        return None
                    data = await response.json()
        except Exception as exc:
            logger.debug("Teams _fetch_email_from_bf_connector failed: %s", exc)
            return None

        # Standard fields: id, name, aadObjectId
        # Teams-specific extension: `properties` may contain `email`
        props = data.get("properties") or data.get("userPrincipalName") or {}
        email = None
        if isinstance(props, dict):
            email = props.get("email") or props.get("userPrincipalName")
        if not email:
            # Some tenants return email directly on the member object
            email = data.get("email") or data.get("userPrincipalName")
        if email:
            logger.info(
                "Teams fetch_sender_profile (BF Connector): resolved user %s → email=%s",
                bf_user_id,
                email,
            )
        return email or None


def _teams_display_resource_card(
    render_plan: SurfaceDisplayRenderPlan,
) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": render_plan.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    if render_plan.summary:
        body.append(
            {
                "type": "TextBlock",
                "text": render_plan.summary,
                "wrap": True,
                "spacing": "Small",
            }
        )
    if render_plan.detail_lines:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {"title": "", "value": line}
                    for line in render_plan.detail_lines[:5]
                    if line
                ],
            }
        )

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    action = render_plan.primary_action
    if action is not None:
        card["actions"] = [
            {
                "type": "Action.OpenUrl",
                "title": action.label,
                "url": action.url,
            }
        ]
    return card


def _teams_question_input(question: SurfaceQuestion) -> dict[str, Any]:
    """An Input.ChoiceSet keyed by the question header; values are option labels."""
    choices = [
        {
            "title": (
                f"{opt.label} (recommended)" if opt.recommended else opt.label
            ),
            "value": opt.label,
        }
        for opt in question.options
    ]
    element: dict[str, Any] = {
        "type": "Input.ChoiceSet",
        "id": question.header,
        "label": question.question,
        "choices": choices,
    }
    if question.multi_select:
        element["isMultiSelect"] = True
    return element


def _teams_question_card(plan: SurfaceQuestionRenderPlan) -> dict[str, Any]:
    """Adaptive Card with Input.ChoiceSet per question (+ optional Other text)
    and an Action.Submit carrying the callback id. Teams merges the input ids
    into the submitted ``value`` keyed by question header."""
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": plan.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    for question in plan.questions:
        body.append(_teams_question_input(question))
        if plan.allow_other:
            body.append(
                {
                    "type": "Input.Text",
                    "id": f"{question.header}{OTHER_ANSWER_SUFFIX}",
                    "label": "Other (type your own)",
                }
            )
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
        "actions": [
            {
                "type": "Action.Submit",
                "title": plan.submit_label,
                "data": {TEAMS_FORM_CALLBACK_KEY: plan.callback_id},
            }
        ],
    }
