"""Everything the Teams adapter sends outward: replies, cards, progress.

Split from the adapter because it is the opposite direction of travel from the
parsing and enrichment left there, and because these are the methods that talk
to two different APIs -- Bot Framework for messages, Graph for everything else.

A layer over :class:`BaseSurfaceAdapter` rather than a mixin beside it: every
method here overrides a real fallback on the base, and a linear chain says which
wins without the reader having to work out an MRO.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp

from app.core.log.log import get_logger
from app.core.net.aiohttp_client import new_aiohttp_session
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.cards import (
    _teams_approval_card,
    _teams_display_resource_card,
    _teams_question_card,
)

logger = get_logger(__name__)


class TeamsSurfaceEgress(BaseSurfaceAdapter):
    """The outbound half of :class:`TeamsSurfaceAdapter`."""

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

    async def send_approval(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as an Adaptive Card with
        Approve/Deny Action.Submit buttons carrying the decision."""
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
                    "content": _teams_approval_card(approval_plan),
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
            async with new_aiohttp_session() as session:
                async with session.post(
                    url,
                    headers=client.auth_headers(bot_token),
                    json={"type": "typing"},
                ):
                    pass  # best-effort, ignore status
        except Exception:
            logger.debug(
                "agent_surfaces.adapter.teams_typing_indicator_best_effort.observed"
            )

    async def stream_progress(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        # Accepted and unused: the PUT edits an activity this bot already owns,
        # so there is no author to set. Declared because the shared caller
        # passes it to whichever adapter answers.
        del credentials, metadata
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
            url = f"{base}/v3/conversations/{quote(str(conversation_id))}/activities"
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
