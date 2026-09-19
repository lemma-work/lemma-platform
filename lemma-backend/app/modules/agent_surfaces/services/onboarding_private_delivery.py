"""Resolve an actual private conversation before sending any signup material."""

from __future__ import annotations

from pydantic import BaseModel, JsonValue

from app.core.net.http_client import get_shared_http_client
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.slack.client import build_slack_client
from app.modules.agent_surfaces.platforms.teams import client as teams_client


class PrivateDeliveryUnavailable(RuntimeError):
    pass


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
        response = await slack.conversations_open(users=actor)
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
        response.raise_for_status()
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
