"""Everything the Teams adapter sends outward: replies, cards, progress.

Split from the adapter because it is the opposite direction of travel from the
parsing and enrichment left there, and because these are the methods that talk
to two different APIs -- Bot Framework for messages, Graph for everything else.

A layer over :class:`BaseSurfaceAdapter` rather than a mixin beside it: every
method here overrides a real fallback on the base, and a linear chain says which
wins without the reader having to work out an MRO.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    TeamsReplyTarget,
    reply_target_as,
    stored_reply_target_as,
)

from app.modules.agent_surfaces.domain.models import ProgressHandle

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials

from typing import Any
from urllib.parse import quote

import httpx

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    ProgressHandleKind,
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.base import BaseSurfaceAdapter
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.delivery import (
    RetryPolicy,
    note_partial_delivery,
    with_retry,
)
from app.modules.agent_surfaces.platforms.outbound_http import outbound_http
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.send_guard import unsendable
from app.modules.agent_surfaces.platforms.teams import client
from app.modules.agent_surfaces.platforms.teams.decision_cards import (
    activity_url,
    remember_teams_card,
    retired_activity,
)
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.domain.errors import AgentSurfacePlatformError
from app.modules.agent_surfaces.platforms.teams.cards import (
    _teams_approval_card,
    _teams_display_resource_card,
    _teams_question_card,
    teams_retry_card,
)

logger = get_logger(__name__)

#: One Bot Framework activity body: a ``type`` plus whichever of ``text``,
#: ``summary``, ``attachments`` and ``replyToId`` the call carries.
type BotFrameworkActivity = dict[str, object]

# Teams rejects a message whose body runs past roughly 28 KB, and the Connector
# answers with a 4xx that loses the whole reply rather than a truncated one.
# Well under it, in characters, so multi-byte text and the markdown that
# formatting adds still clear it.
_TEAMS_TEXT_LIMIT = 20_000

#: The total each Bot Framework call had as its own aiohttp session's timeout.
_BOT_FRAMEWORK_TIMEOUT = 30.0
#: The shared default a best-effort call (typing, acknowledgement) had.
_BEST_EFFORT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class TeamsSurfaceEgress(BaseSurfaceAdapter):
    """The outbound half of :class:`TeamsSurfaceAdapter`."""

    #: Overridden per instance only by tests that must not wait out a backoff.
    _retry_policy = RetryPolicy()

    async def _post_activity(
        self, url: str, *, token: str, body: BotFrameworkActivity
    ) -> str | None:
        """Post one Bot Framework activity, retrying what is worth retrying.

        The Connector throttles, and a throttled reply used to raise straight
        out of ``raise_for_status()``: recorded as undelivered, never sent
        again. Routed through the same ``with_retry`` seam Telegram and Slack
        use, so one policy covers every platform.
        """

        async def send() -> str | None:
            async with outbound_http(_BOT_FRAMEWORK_TIMEOUT) as http:
                response = await http.post(
                    url, headers=client.auth_headers(token), json=body
                )
            response.raise_for_status()
            # The activity id is how it is edited later (a card's buttons
            # taken away with a PUT). Best-effort: a body that is not the
            # documented JSON leaves it unknown.
            try:
                sent = response.json()
            except ValueError:
                return None
            activity_id = sent.get("id") if isinstance(sent, dict) else None
            return str(activity_id) if activity_id else None

        return await with_retry(
            send,
            policy=self._retry_policy,
            classify=client.classify_teams_error,
            retry_after=client.teams_retry_after,
        )

    async def send_message(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Send a reply via the Bot Framework Connector API; the id of its
        first activity.

        This uses Lemma's own bot credentials (from settings) together with the
        customer's tenant_id to acquire a Bot Framework token, then posts the
        reply to the correct conversation via the serviceUrl captured from the
        original incoming activity.
        """
        del credentials
        url, token = await self._activities_endpoint(event, metadata)
        reply_to_id = reply_target_as(event, TeamsReplyTarget).reply_to_id
        card = (metadata or {}).get("onboarding_card")
        chunks = chunk_text(message, limit=_TEAMS_TEXT_LIMIT) or [message]
        first: str | None = None
        for index, chunk in enumerate(chunks):
            body: dict[str, Any] = {
                "type": "message",
                "text": chunk,
                # Teams only renders Markdown when the Bot Framework activity
                # explicitly declares Markdown text.
                "textFormat": "markdown",
            }
            if reply_to_id:
                body["replyToId"] = reply_to_id
            if index == 0 and event.is_dm and card:
                body["attachments"] = [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": card,
                    }
                ]
            if index == len(chunks) - 1 and (metadata or {}).get("retry_action"):
                body["attachments"] = [
                    {
                        "contentType": "application/vnd.microsoft.card.adaptive",
                        "content": teams_retry_card(event.external_thread_id),
                    }
                ]
            try:
                activity_id = await self._post_activity(url, token=token, body=body)
            except platform_transport_errors():
                if index:
                    note_partial_delivery()
                    logger.warning(
                        "agent_surfaces.adapter.teams_message_partially_delivered.degraded",
                        sent_chunks=index,
                        total_chunks=len(chunks),
                        exc_info=True,
                    )
                raise
            first = first or activity_id
        return first

    async def _activities_endpoint(
        self, event: ParsedInboundSurfaceEvent, metadata: dict[str, Any] | None
    ) -> tuple[str, str]:
        """Where this reply is posted and the token to post it with, or raise.

        Raises rather than returning: a send with no tenant, token or
        conversation used to return quietly and the caller recorded whatever
        does not raise as delivered. Telegram and Resend raise here.
        """
        tenant_id = event.tenant_id
        token = await self._get_bot_token(tenant_id) if tenant_id else None
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if tenant_id and token and conversation_id:
            url = (
                f"{client.bf_service_url(reply_target_as(event, TeamsReplyTarget).service_url)}"
                f"/v3/conversations/{quote(str(conversation_id))}/activities"
            )
            return url, token
        if (metadata or {}).get("private_onboarding"):
            raise RuntimeError(
                "Teams installation has no tenant"
                if not tenant_id
                else "Teams installation cannot deliver private onboarding"
                if not token
                else "Teams installation has no personal conversation"
            )
        raise unsendable(
            "Teams",
            tenant_id=tenant_id,
            bot_token=token,
            conversation_id=conversation_id,
        )

    async def _render_resource(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Post a resource as an Adaptive Card. Always a card, so always True.

        It answered ``None``, which the delivery ladder reads as "not native":
        every resource on Teams was recorded DEGRADED even when the card
        rendered.
        """
        del credentials, metadata
        url, token = await self._activities_endpoint(event, None)
        reply_to_id = reply_target_as(event, TeamsReplyTarget).reply_to_id
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

        await self._post_activity(url, token=token, body=body)
        return True

    async def _render_choices(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        del credentials, metadata
        tenant_id = event.tenant_id
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not tenant_id or not conversation_id:
            return False
        token = await self._get_bot_token(tenant_id)
        if not token:
            return False

        url = (
            f"{client.bf_service_url(reply_target_as(event, TeamsReplyTarget).service_url)}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        card = _teams_question_card(question_plan)
        body: dict[str, Any] = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }
        reply_to_id = reply_target_as(event, TeamsReplyTarget).reply_to_id
        if reply_to_id:
            body["replyToId"] = reply_to_id

        activity_id = await self._post_activity(url, token=token, body=body)
        await remember_teams_card(question_plan.callback_id, activity_id, event, card)
        return True

    async def _render_decision(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as an Adaptive Card with
        Approve/Deny Action.Submit buttons carrying the decision."""
        del credentials, metadata
        tenant_id = event.tenant_id
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not tenant_id or not conversation_id:
            return False
        token = await self._get_bot_token(tenant_id)
        if not token:
            return False

        url = (
            f"{client.bf_service_url(reply_target_as(event, TeamsReplyTarget).service_url)}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        card = _teams_approval_card(approval_plan)
        body: dict[str, Any] = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }
        reply_to_id = reply_target_as(event, TeamsReplyTarget).reply_to_id
        if reply_to_id:
            body["replyToId"] = reply_to_id

        activity_id = await self._post_activity(url, token=token, body=body)
        await remember_teams_card(approval_plan.callback_id, activity_id, event, card)
        return True

    async def retire_decision_card(
        self,
        *,
        credentials: SurfaceCredentials,
        handle: DecisionCardHandle,
        reason: CardRetirement = CardRetirement.SUPERSEDED,
    ) -> None:
        """Replace a finished pause's card with its words and no actions."""
        del credentials
        token = await self._get_bot_token(handle.extra.get("tenant_id") or "")
        if not token:
            raise AgentSurfacePlatformError("TEAMS", "bot token unavailable")
        async with outbound_http(_BOT_FRAMEWORK_TIMEOUT) as http:
            response = await http.put(
                activity_url(handle),
                headers=client.auth_headers(token),
                json=retired_activity(handle, reason),
            )
        response.raise_for_status()

    async def add_processing_indicator(
        self,
        *,
        credentials: SurfaceCredentials,
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

        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not conversation_id:
            return

        url = (
            f"{client.bf_service_url(reply_target_as(event, TeamsReplyTarget).service_url)}"
            f"/v3/conversations/{quote(str(conversation_id))}/activities"
        )
        try:
            async with outbound_http(_BEST_EFFORT_TIMEOUT) as http:
                # Best-effort: the status is not read.
                await http.post(
                    url,
                    headers=client.auth_headers(bot_token),
                    json={"type": "typing"},
                )
        except httpx.HTTPError, TimeoutError:
            logger.debug(
                "agent_surfaces.adapter.teams_typing_indicator_best_effort.observed"
            )

    async def acknowledge_interaction(
        self,
        *,
        credentials: SurfaceCredentials,
        interaction: ParsedSurfaceInteraction,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        """Answer a tapped Adaptive Card action.

        Teams edits an activity with ``PUT .../activities/{id}``, and the
        submission tells us which activity the card was in (``replyToId``).
        Replacing it with a plain line is what retires the buttons -- an
        Adaptive Card's actions stay tappable forever otherwise, and a second
        tap on a settled decision reached a run that had already moved on.

        Falls back to posting a new message when the card cannot be edited, so
        the outcome is said either way. Best-effort throughout: the decision is
        already recorded before this runs.
        """
        del credentials, show_alert
        target = stored_reply_target_as(
            "TEAMS", interaction.reply_target, TeamsReplyTarget
        )
        conversation_id = target.conversation_id
        if not conversation_id:
            return
        token = await self._get_bot_token(interaction.tenant_id)
        if not token:
            return

        activities = (
            f"{client.bf_service_url(target.service_url)}"
            f"/v3/conversations/{quote(conversation_id)}/activities"
        )
        body = {"type": "message", "text": (text or "").strip() or "Done"}
        activity_id = target.reply_to_id if clear_actions else None
        try:
            async with outbound_http(_BEST_EFFORT_TIMEOUT) as http:
                if activity_id:
                    edited = await http.put(
                        f"{activities}/{quote(activity_id)}",
                        headers=client.auth_headers(token),
                        json=body,
                    )
                    if edited.status_code < 400:
                        return
                await http.post(
                    activities, headers=client.auth_headers(token), json=body
                )
        except httpx.HTTPError, TimeoutError:
            logger.debug(
                "agent_surfaces.adapter.teams_interaction_acknowledgement_best.observed"
            )

    async def stream_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: ProgressHandle | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProgressHandle | None:
        # Accepted and unused: the PUT edits an activity this bot already owns,
        # so there is no author to set. Declared because the shared caller
        # passes it to whichever adapter answers.
        del credentials, metadata
        tenant_id = event.tenant_id
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not tenant_id or not conversation_id:
            return progress_handle
        bot_token = await self._get_bot_token(tenant_id)
        if not bot_token:
            return progress_handle
        base = client.bf_service_url(
            reply_target_as(event, TeamsReplyTarget).service_url
        )
        activity_id = progress_handle.message_id if progress_handle else None
        body = {"type": "message", "text": progress_text}
        try:
            if activity_id:
                url = (
                    f"{base}/v3/conversations/{quote(str(conversation_id))}"
                    f"/activities/{quote(str(activity_id))}"
                )
                async with outbound_http(_BOT_FRAMEWORK_TIMEOUT) as http:
                    response = await http.put(
                        url, headers=client.auth_headers(bot_token), json=body
                    )
                response.raise_for_status()
                return progress_handle
            url = f"{base}/v3/conversations/{quote(str(conversation_id))}/activities"
            async with outbound_http(_BOT_FRAMEWORK_TIMEOUT) as http:
                response = await http.post(
                    url, headers=client.auth_headers(bot_token), json=body
                )
            response.raise_for_status()
            data = response.json()
            new_id = (data or {}).get("id")
            return (
                ProgressHandle(kind=ProgressHandleKind.MESSAGE, message_id=str(new_id))
                if new_id
                else progress_handle
            )
        except Exception:
            return progress_handle

    async def end_progress(
        self,
        *,
        credentials: SurfaceCredentials,
        event: ParsedInboundSurfaceEvent,
        progress_handle: ProgressHandle | None = None,
    ) -> None:
        """Delete the streamed progress ("thinking") activity at run end so it
        does not linger next to the final answer (the final answer is delivered
        separately). Mirrors Slack/Telegram end_progress. Best-effort."""
        del credentials
        activity_id = progress_handle.message_id if progress_handle else None
        tenant_id = event.tenant_id
        conversation_id = reply_target_as(event, TeamsReplyTarget).conversation_id
        if not activity_id or not tenant_id or not conversation_id:
            return
        bot_token = await self._get_bot_token(tenant_id)
        if not bot_token:
            return
        base = client.bf_service_url(
            reply_target_as(event, TeamsReplyTarget).service_url
        )
        url = (
            f"{base}/v3/conversations/{quote(str(conversation_id))}"
            f"/activities/{quote(str(activity_id))}"
        )
        try:
            async with outbound_http(_BOT_FRAMEWORK_TIMEOUT) as http:
                response = await http.delete(
                    url, headers=client.auth_headers(bot_token)
                )
            response.raise_for_status()
        except httpx.HTTPError, TimeoutError:
            return
