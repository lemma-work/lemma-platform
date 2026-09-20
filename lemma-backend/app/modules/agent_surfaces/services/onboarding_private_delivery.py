"""Resolve an actual private conversation before sending any signup material."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

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


#: Builds the Slack web client for a set of credentials.
SlackClientFactory = Callable[[dict[str, JsonValue]], Awaitable[Any]]
#: The shared outbound HTTP client.
HttpClientFactory = Callable[[], Any]
#: Fetches a Bot Framework token for this deployment's Teams app.
BotTokenFactory = Callable[[], Awaitable["str | None"]]


class _ConversationCreated(BaseModel):
    id: str


async def _slack_private_destination(
    event: ParsedInboundSurfaceEvent,
    actor: str,
    credentials: dict[str, JsonValue],
    open_slack: SlackClientFactory,
) -> tuple[str, dict[str, JsonValue]]:
    """The DM channel Slack opens with this person, and where to reply in it."""
    slack = await open_slack(credentials)
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
        raise PrivateDeliveryUnavailable("Slack could not open a personal conversation")
    destination_id: str = channel["id"]
    return destination_id, {"channel": destination_id}


async def _teams_private_destination(
    event: ParsedInboundSurfaceEvent,
    open_http: HttpClientFactory,
    fetch_token: BotTokenFactory,
) -> tuple[str, dict[str, JsonValue]]:
    """The personal conversation the Bot Framework creates, and its service URL."""
    sender = event.raw_payload.get("from")
    recipient = event.raw_payload.get("recipient")
    if not isinstance(sender, dict) or not isinstance(recipient, dict):
        raise PrivateDeliveryUnavailable("Teams needs a personal bot installation")
    sender_id, bot_id = sender.get("id"), recipient.get("id")
    if not isinstance(sender_id, str) or not isinstance(bot_id, str):
        raise PrivateDeliveryUnavailable(
            "Teams did not provide the bot conversation identities"
        )
    token = await fetch_token()
    if not token:
        raise PrivateDeliveryUnavailable(
            "The Teams bot installation cannot send privately"
        )
    service_url = teams_client.bf_service_url(event.reply_target.get("service_url"))
    response = await open_http().post(
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
        # Only the answered-and-refused case. A timeout or a connection error is
        # a different fact -- the Bot Framework may well be there on the next
        # attempt -- so those still propagate and are retried.
        raise PrivateDeliveryUnavailable(
            "Teams refused to create the personal conversation: "
            f"{refused.response.status_code}"
        ) from refused
    destination_id = _ConversationCreated.model_validate(response.json()).id
    return destination_id, {
        "service_url": service_url,
        "conversation_id": destination_id,
    }


async def private_onboarding_destination(
    event: ParsedInboundSurfaceEvent,
    *,
    credentials: dict[str, JsonValue],
    slack_client: SlackClientFactory | None = None,
    http_client: HttpClientFactory | None = None,
    bot_token: BotTokenFactory | None = None,
) -> ParsedInboundSurfaceEvent:
    """Open a private conversation with the sender, or say why there is none.

    The three provider seams are parameters rather than module globals a test
    reaches in and replaces. What this function decides is which refusals become
    `PrivateDeliveryUnavailable` and which stay transport failures, and that
    decision is worth checking without a Slack workspace or a Bot Framework
    endpoint -- so the caller passes what answers.

    `None` rather than the real callable as the default, which is the rule
    `scripts/check_import_bound_defaults.py` enforces: a default is evaluated
    once at import, so a suite that patches `get_shared_http_client` on this
    module would not reach one, and the test would keep passing while talking to
    the real client. Resolved here, at call time, the patch lands.

    The two platforms sit in their own functions because what is left here is
    the part that is the same for both -- already private, no sender to open
    with, a platform that cannot do this at all -- and inlining either one
    buried that under a provider call.
    """
    open_slack: SlackClientFactory = slack_client or build_slack_client
    open_http: HttpClientFactory = http_client or get_shared_http_client
    fetch_token: BotTokenFactory = bot_token or teams_client.get_bot_token
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
        destination_id, reply_target = await _slack_private_destination(
            event, actor, credentials, open_slack
        )
    elif event.platform == SurfacePlatform.TEAMS:
        destination_id, reply_target = await _teams_private_destination(
            event, open_http, fetch_token
        )
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
