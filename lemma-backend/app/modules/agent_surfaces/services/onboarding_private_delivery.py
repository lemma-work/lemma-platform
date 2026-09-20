"""Resolve an actual private conversation before sending any signup material."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, JsonValue
from slack_sdk.errors import SlackApiError

from app.core.net.http_client import get_shared_http_client
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.slack.client import build_slack_client
from app.modules.agent_surfaces.platforms.teams import client as teams_client


class PrivateDeliveryUnavailable(RuntimeError):
    """This person cannot be taken aside, so signup cannot happen here.

    Raised for every reason a private conversation cannot be opened, including
    the provider saying no. That last part is the point: the shape checks below
    raised this and the actual calls raised ``SlackApiError`` and
    ``httpx.HTTPStatusError``, which is the same fact wearing two types the one
    caller that matters did not both catch.

    ``events.handlers._context_for_delivery`` catches this and falls through to
    ordinary ingestion, which answers a stranger with "please sign up". A
    workspace whose app was installed without ``im:write`` took the other path:
    the error propagated out of the handler, ingestion never ran, and the reply
    never fired -- on every message that person ever sent, forever. Silence was
    the one outcome worth ruling out, and it was the one they got.
    """


class _ConversationCreated(BaseModel):
    id: str


async def private_onboarding_destination(
    event: ParsedInboundSurfaceEvent,
    *,
    credentials: dict[str, JsonValue],
) -> ParsedInboundSurfaceEvent:
    if event.is_dm:
        return event.model_copy(
            update={"message_text": "", "metadata": {}, "raw_payload": {}}
        )
    actor = event.sender_external_user_id
    if not actor or not event.tenant_id:
        raise PrivateDeliveryUnavailable(
            "The installation has no private sender context"
        )
    if event.platform == SurfacePlatform.SLACK:
        slack = await build_slack_client(credentials)
        try:
            response = await slack.conversations_open(users=actor)
        except SlackApiError as refused:
            # ``missing_scope`` (no ``im:write``), ``user_not_found``,
            # ``cannot_dm_bot`` -- all of them mean the same thing here, and
            # none of them gets better on a retry.
            raise PrivateDeliveryUnavailable(
                f"Slack refused to open a personal conversation: {refused}"
            ) from refused
        channel = response.get("channel")
        if not isinstance(channel, dict) or not isinstance(channel.get("id"), str):
            raise PrivateDeliveryUnavailable(
                "Slack could not open a personal conversation"
            )
        destination_id = channel["id"]
        reply_target = {"channel": destination_id}
    elif event.platform == SurfacePlatform.TEAMS:
        sender = event.raw_payload.get("from")
        recipient = event.raw_payload.get("recipient")
        if not isinstance(sender, dict) or not isinstance(recipient, dict):
            raise PrivateDeliveryUnavailable("Teams needs a personal bot installation")
        sender_id, bot_id = sender.get("id"), recipient.get("id")
        if not isinstance(sender_id, str) or not isinstance(bot_id, str):
            raise PrivateDeliveryUnavailable(
                "Teams did not provide the bot conversation identities"
            )
        token = await teams_client.get_bot_token()
        if not token:
            raise PrivateDeliveryUnavailable(
                "The Teams bot installation cannot send privately"
            )
        service_url = teams_client.bf_service_url(event.reply_target.get("service_url"))
        response = await get_shared_http_client().post(
            f"{service_url}/v3/conversations",
            headers=teams_client.auth_headers(token),
            json={
                "isGroup": False,
                "bot": {"id": bot_id},
                "members": [{"id": sender_id}],
                "tenantId": event.tenant_id,
                "channelData": {"tenant": {"id": event.tenant_id}},
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as refused:
            # Only the answered-and-refused case. A timeout or a connection
            # error is a different fact -- the Bot Framework may well be there
            # on the next attempt -- so those still propagate and are retried.
            raise PrivateDeliveryUnavailable(
                "Teams refused to create the personal conversation: "
                f"{refused.response.status_code}"
            ) from refused
        destination_id = _ConversationCreated.model_validate(response.json()).id
        reply_target = {"service_url": service_url, "conversation_id": destination_id}
    else:
        raise PrivateDeliveryUnavailable(
            "Open a private conversation with the Lemma bot"
        )
    return event.model_copy(
        update={
            "is_dm": True,
            "conversation_type": ConversationType.EXTERNAL_DM,
            "external_channel_id": destination_id,
            "external_thread_id": destination_id,
            "reply_target": reply_target,
            "message_text": "",
            "metadata": {},
            "raw_payload": {},
        }
    )
