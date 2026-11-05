from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    SlackReplyTarget,
    reply_target_as,
)

from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    SlackCredentials,
)

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx

from app.modules.agent_surfaces.platforms.outbound_http import outbound_http
from app.modules.agent_surfaces.platforms.slack.reply_blocks import reply_blocks
from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.send_guard import unsendable

from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
    fallback_text,
)
from app.modules.agent_surfaces.platforms.slack.channel_reads import (
    SlackChannelReadsMixin,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    _approval_blocks,
    _display_resource_blocks,
    PROCESSING_LOADING_TEXT,
    PROCESSING_STATUS_TEXT,
    _question_blocks,
    question_blocks_fit,
    slack_acknowledgement_body,
)
from app.modules.agent_surfaces.platforms.delivery import (
    RetryPolicy,
    note_partial_delivery,
    with_retry,
)
from app.modules.agent_surfaces.platforms.slack.streaming import (
    remove_processing_reaction,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    classify_slack_error,
    slack_access_token,
    slack_customized_message_kwargs,
    slack_retry_after,
    slack_scopes,
)
from app.modules.agent_surfaces.platforms.slack.decision_cards import (
    remember_slack_card,
    retire_slack_card,
)
from app.modules.agent_surfaces.platforms.slack.files import SlackFilesMixin
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.core.log.log import get_logger

if TYPE_CHECKING:
    from slack_sdk.web.async_client import AsyncWebClient

logger = get_logger(__name__)

#: One ``chat.postMessage`` body. Slack takes a different set of keys per call
#: (blocks, thread_ts, the ``chat:write.customize`` identity pair), and the
#: values are whatever that key's shape is, so the value type stays open --
#: naming the mapping is what the retry helper needs.
type SlackMessagePayload = dict[str, object]


def _ephemeral_target(metadata: dict[str, object] | None) -> str:
    """Who a reply should be shown to alone, or "" for the whole channel.

    Set only by the fallback path, which answers a newcomer in a channel
    without making the channel read it.
    """
    return str((metadata or {}).get("ephemeral_to") or "").strip()


class SlackPlatformService(SlackChannelReadsMixin, SlackFilesMixin):
    def __init__(self, *, credentials: CredentialsLike, parser=None) -> None:
        if parser is None:
            from app.modules.agent_surfaces.platforms.slack.parser import (
                SlackMessageParser,
            )

            parser = SlackMessageParser()
        self.credentials = SlackCredentials.of(credentials)
        self.parser = parser
        self._retry_policy = RetryPolicy()

    async def _post_message(
        self, client: AsyncWebClient, payload: SlackMessagePayload
    ) -> str | None:
        """Post one message, retrying the failures Slack expects us to retry.

        Returns the posted message's ``ts``, or None for an ephemeral.

        Slack limits ``chat.postMessage`` per channel and answers a throttled
        call with 429 and a ``Retry-After`` header. Without this the throttle
        was caught upstream as a transport error, recorded as UNDELIVERED, and
        the answer was never sent again -- and a chunked answer left the person
        reading the first part of something the system had written off. Uses
        the same ``with_retry`` seam Telegram does, so there is one retry
        policy across the platforms rather than two.

        Carrying an ``ephemeral_user`` sends it to that one person instead, for
        the fallback path answering a newcomer in a channel the rest of which
        should not have to read it. An ephemeral is not a stored message -- it
        does not survive a reload and Slack does not guarantee it was shown --
        which is the right trade for telling somebody how to get access and the
        wrong one for anything that has to be answered later.
        """
        user = str(payload.pop("ephemeral_user", "") or "")
        if not user:
            # One id for every attempt at this message, so a retry after a
            # 5xx Slack had in fact accepted names the post it repeats.
            payload.setdefault("client_msg_id", str(uuid4()))
        send = (
            (lambda: client.chat_postEphemeral(user=user, **payload))
            if user
            else (lambda: client.chat_postMessage(**payload))
        )
        response = await with_retry(
            send,
            policy=self._retry_policy,
            classify=classify_slack_error,
            retry_after=slack_retry_after,
        )
        # The message's `ts` is how it is edited later (a card's buttons taken
        # away with `chat.update`). An ephemeral has none worth keeping.
        if user:
            return None
        ts = response.get("ts") if response is not None else None
        return str(ts) if ts else None

    async def fetch_sender_profile(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
    ) -> SurfaceSenderProfile | None:
        user_id = event.sender_external_user_id
        token = slack_access_token(self.credentials)
        if not user_id or not token:
            logger.debug(
                "agent_surfaces.service.slack_fetch_sender_profile_skipped.diagnostic",
                user_id=user_id,
            )
            return None

        client = await build_slack_client(self.credentials)
        try:
            response = await client.users_info(user=user_id)
            user = response.get("user") or {}
            profile = user.get("profile") or {}
            return SurfaceSenderProfile(
                external_user_id=user.get("id") or user_id,
                email=profile.get("email"),
                phone=profile.get("phone"),
                display_name=profile.get("display_name") or profile.get("real_name"),
                raw_profile=user,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_fetch_sender_profile_user.propagated",
                user_id=user_id,
                exc_info=True,
            )
            raise

    async def get_user_display_name(self, user_id: str) -> str | None:
        """Return a user's Slack display name (best-effort).

        Used to surface the bot's own human-facing name for the reach handle.
        Returns None when the token or user id is missing, or on any API error.
        """
        token = slack_access_token(self.credentials)
        if not user_id or not token:
            return None
        try:
            client = await build_slack_client(self.credentials)
            response = await client.users_info(user=user_id)
            user = response.get("user") or {}
            profile = user.get("profile") or {}
            name = profile.get("display_name") or profile.get("real_name")
            return str(name).strip() or None if name else None
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_get_user_display_name.observed",
                user_id=user_id,
            )
            return None

    async def send_message(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Post the message; the ``ts`` of its first part (None for an
        ephemeral), which a reply in its thread names."""
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        if not token or not channel:
            if (metadata or {}).get("private_onboarding"):
                raise RuntimeError(
                    "Slack installation cannot deliver private onboarding"
                )
            # Raised, not logged and returned: the caller records whatever does
            # not raise as delivered, so a send with no token or no channel
            # reported NATIVE having sent nothing. Telegram and Resend raise.
            raise unsendable("Slack", access_token=token, channel=channel)

        client = await build_slack_client(self.credentials)
        thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
        identity_kwargs = slack_customized_message_kwargs(
            self.credentials,
            (metadata or {}).get("agent_display_name"),
            (metadata or {}).get("agent_icon_url"),
        )
        # Slack caps markdown blocks at 12,000 characters *per payload*, so a
        # long answer becomes several messages rather than one truncated one.
        chunks = chunk_text(message, limit=MARKDOWN_BLOCK_CHAR_LIMIT) or [message]
        # Empty for a normal channel post; `_post_message` decides on it.
        ephemeral_user = _ephemeral_target(metadata)
        index = 0
        first: str | None = None
        try:
            for index, chunk in enumerate(chunks):
                blocks = reply_blocks(
                    chunk,
                    metadata,
                    is_dm=event.is_dm,
                    is_last=index == len(chunks) - 1,
                )
                payload: dict[str, Any] = {
                    "channel": channel,
                    "text": fallback_text(chunk),
                    "blocks": blocks,
                }
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                payload.update(identity_kwargs)
                payload["ephemeral_user"] = ephemeral_user
                ts = await self._post_message(client, payload)
                first = first or ts
        except Exception:
            if index:
                note_partial_delivery()
            logger.debug(
                "agent_surfaces.service.slack_send_message_channel_s.propagated",
                exc_info=True,
            )
            raise
        if not ephemeral_user:
            await remove_processing_reaction(self.credentials, event)
        return first

    async def acknowledge_interaction(
        self,
        interaction: ParsedSurfaceInteraction,
        *,
        text: str | None,
        show_alert: bool,
        clear_actions: bool,
    ) -> None:
        """Answer a tapped button where it was tapped.

        ``response_url`` is the right instrument for this: Slack mints one per
        interaction, it needs no token and no scope, and ``replace_original``
        rewrites the very message the button was in. It expires after 30
        minutes, which is longer than any tap-to-decision gap.

        Best-effort by construction. The decision has already been recorded by
        the time this runs, so a failed acknowledgement must never raise and
        undo it -- but until this existed, a tap on Slack produced no
        confirmation, left the buttons live, and reported a failure to nobody.
        """
        del show_alert  # Slack has no modal alert outside a trigger_id flow.
        response_url = str(
            (interaction.raw_payload or {}).get("response_url") or ""
        ).strip()
        if not response_url:
            return
        try:
            async with outbound_http(10.0) as http_client:
                await http_client.post(
                    response_url,
                    json=slack_acknowledgement_body(
                        (interaction.raw_payload or {}).get("message") or {},
                        text=text,
                        clear_actions=clear_actions,
                    ),
                )
        except httpx.HTTPError, httpx.InvalidURL:
            logger.debug(
                "agent_surfaces.service.slack_interaction_acknowledgement_best.observed"
            )

    async def _render_resource(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Post a resource as a Block Kit card. Always a card, so always True."""
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        if not token or not channel:
            raise unsendable("Slack", access_token=token, channel=channel)

        client = await build_slack_client(self.credentials)
        try:
            payload: dict[str, Any] = {
                "channel": channel,
                # Notification-fallback text only (blocks carry the button). Use
                # the caption so the raw URL isn't surfaced in the push preview.
                "text": render_plan.to_caption(),
                "blocks": _display_resource_blocks(render_plan),
            }
            thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
            if thread_ts:
                payload["thread_ts"] = thread_ts
            payload.update(
                slack_customized_message_kwargs(
                    self.credentials,
                    (metadata or {}).get("agent_display_name"),
                    (metadata or {}).get("agent_icon_url"),
                )
            )
            await self._post_message(client, payload)
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_send_display_resource_channel.propagated",
                exc_info=True,
            )
            raise
        return True

    async def _render_choices(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render ask_user questions as Block Kit selects + a Submit button."""
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        if not token or not channel or not question_blocks_fit(question_plan):
            return False
        client = await build_slack_client(self.credentials)
        payload: dict[str, Any] = {
            "channel": channel,
            "text": question_plan.title,
            "blocks": _question_blocks(question_plan),
        }
        thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
        if thread_ts:
            payload["thread_ts"] = thread_ts
        payload.update(
            slack_customized_message_kwargs(
                self.credentials,
                (metadata or {}).get("agent_display_name"),
                (metadata or {}).get("agent_icon_url"),
            )
        )
        ts = await self._post_message(client, payload)
        await remember_slack_card(question_plan.callback_id, ts, payload)
        return True

    async def _render_decision(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as Block Kit Approve/Deny buttons."""
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        if not token or not channel:
            return False
        client = await build_slack_client(self.credentials)
        payload: dict[str, Any] = {
            "channel": channel,
            "text": f"Approval needed: {approval_plan.title}",
            "blocks": _approval_blocks(approval_plan),
        }
        thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
        if thread_ts:
            payload["thread_ts"] = thread_ts
        payload.update(
            slack_customized_message_kwargs(
                self.credentials,
                (metadata or {}).get("agent_display_name"),
                (metadata or {}).get("agent_icon_url"),
            )
        )
        ts = await self._post_message(client, payload)
        await remember_slack_card(approval_plan.callback_id, ts, payload)
        return True

    async def retire_decision_card(
        self, handle: DecisionCardHandle, reason: CardRetirement
    ) -> None:
        """Rewrite a finished pause's card without its controls."""
        await retire_slack_card(
            await build_slack_client(self.credentials), handle, reason
        )

    async def add_processing_indicator(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata  # Part of the adapter interface; the bubble text is fixed.
        token = slack_access_token(self.credentials)
        channel = reply_target_as(event, SlackReplyTarget).channel
        timestamp = event.external_message_id
        thread_ts = reply_target_as(event, SlackReplyTarget).thread_ts
        if not token or not channel or not timestamp:
            logger.debug(
                "agent_surfaces.service.slack_add_processing_indicator_skipped.diagnostic"
            )
            return

        client = await build_slack_client(self.credentials)
        try:
            if (
                event.is_dm
                and thread_ts
                and "assistant:write" in slack_scopes(self.credentials)
            ):
                try:
                    await client.assistant_threads_setStatus(
                        channel_id=str(channel),
                        thread_ts=str(thread_ts),
                        status=PROCESSING_STATUS_TEXT,
                        loading_messages=[PROCESSING_LOADING_TEXT],
                    )
                    return
                except slack_api_error() as exc:
                    error_code = str((exc.response or {}).get("error") or "")
                    if error_code in {
                        "missing_scope",
                        "invalid_arguments",
                        "method_not_supported_for_channel_type",
                    }:
                        # Not an assistant thread (or the scope was revoked) —
                        # fall through to the reaction below rather than
                        # returning, or the DM shows no indicator at all while
                        # the agent works.
                        logger.debug(
                            "agent_surfaces.service.slack_typing_indicator_unsupported_channel.diagnostic",
                            error_code=error_code,
                        )
                    else:
                        raise
            await client.reactions_add(
                channel=str(channel),
                name="eyes",
                timestamp=str(timestamp),
            )
        except slack_api_error() as exc:
            error_code = str((exc.response or {}).get("error") or "")
            if error_code in {"already_reacted", "missing_scope", "not_reactable"}:
                logger.debug(
                    "agent_surfaces.service.slack_reaction_indicator_skipped_channel.diagnostic",
                    error_code=error_code,
                )
                return
            logger.debug(
                "agent_surfaces.service.slack_add_processing_indicator_channel.propagated",
                exc_info=True,
            )
            raise
