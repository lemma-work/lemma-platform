"""The Slack calls the manager makes: views, ephemerals, DMs, and inviting bots.

Each takes the credentials of the bot making the call -- the manager's own for
its Home, its prompts and its invites -- and answers with Slack's error code
rather than raising, because every caller here has something better to say to
a person than a stack trace. A refusal is logged as ``.degraded`` with Slack's
code, so a Home that never appears leaves a trace.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.credentials import CredentialsLike
from app.modules.agent_surfaces.platforms.slack.client import build_slack_client
from app.modules.agent_surfaces.platforms.slack.errors import slack_api_error

if TYPE_CHECKING:
    from slack_sdk.errors import SlackApiError

logger = get_logger(__name__)

Credentials = CredentialsLike
Blocks = Sequence[Mapping[str, object]]

#: Slack's codes for "not now" rather than "no": a durable send retries them.
TRANSIENT_ERRORS = frozenset(
    {
        "ratelimited",
        "internal_error",
        "fatal_error",
        "request_timeout",
        "service_unavailable",
        "unknown_error",
    }
)


def _blocks(blocks: Blocks | None) -> list[dict[str, object]] | None:
    """What the SDK takes: concrete dicts, or nothing."""
    return [dict(block) for block in blocks] if blocks else None


def _error(exc: SlackApiError) -> str:
    data = getattr(getattr(exc, "response", None), "data", None)
    return (
        str(data.get("error") or "unknown_error")
        if isinstance(data, dict)
        else "unknown_error"
    )


def _refused(call: str, exc: SlackApiError) -> str:
    code = _error(exc)
    logger.warning(
        "agent_surfaces.slack_manager.call_refused.degraded",
        call=call,
        error=code,
        exc_info=exc,
    )
    return code


async def publish_home(
    credentials: Credentials, *, user_id: str, view: Mapping[str, object]
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.views_publish(user_id=user_id, view=dict(view))
    except slack_api_error() as exc:
        return _refused("views.publish", exc)
    return None


async def open_modal(
    credentials: Credentials, *, trigger_id: str, view: Mapping[str, object]
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.views_open(trigger_id=trigger_id, view=dict(view))
    except slack_api_error() as exc:
        return _refused("views.open", exc)
    return None


async def update_view(
    credentials: Credentials,
    *,
    view_id: str,
    view: Mapping[str, object],
) -> str | None:
    """Replace an open modal: from "making the app" to "allow it", say."""
    try:
        client = await build_slack_client(credentials)
        await client.views_update(view_id=view_id, view=dict(view))
    except slack_api_error() as exc:
        return _refused("views.update", exc)
    return None


async def post_ephemeral(
    credentials: Credentials,
    *,
    channel: str,
    user: str,
    text: str,
    blocks: Blocks | None = None,
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.chat_postEphemeral(
            channel=channel,
            user=user,
            text=text,
            blocks=_blocks(blocks),
        )
    except slack_api_error() as exc:
        return _refused("chat.postEphemeral", exc)
    return None


async def post_message(
    credentials: Credentials, *, channel: str, text: str, blocks: Blocks | None = None
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.chat_postMessage(
            channel=channel, text=text, blocks=_blocks(blocks)
        )
    except slack_api_error() as exc:
        return _refused("chat.postMessage", exc)
    return None


async def respond(
    response_url: str, *, text: str, blocks: Blocks | None = None
) -> None:
    """Replace the ephemeral a button was tapped in. Slack's response URL needs
    no token: it is the credential, good for a few uses within 30 minutes."""
    from slack_sdk.webhook.async_client import AsyncWebhookClient

    try:
        await AsyncWebhookClient(response_url).send(
            text=text, blocks=_blocks(blocks), replace_original=True
        )
    except slack_api_error(), OSError:
        logger.warning(
            "agent_surfaces.slack_manager.respond_failed.degraded", exc_info=True
        )


async def invite(
    credentials: Credentials, *, channel: str, user_ids: Sequence[str]
) -> str | None:
    """Invite users -- another app's bot included -- into a channel the caller
    is in. ``channels:manage`` covers a public channel, ``groups:write`` a
    private one, so the manager needs no other way in."""
    try:
        client = await build_slack_client(credentials)
        await client.conversations_invite(channel=channel, users=",".join(user_ids))
    except slack_api_error() as exc:
        if _error(exc) == "already_in_channel":
            return None
        return _refused("conversations.invite", exc)
    return None


async def leave_channel(credentials: Credentials, channel: str) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.conversations_leave(channel=channel)
    except slack_api_error() as exc:
        return _refused("conversations.leave", exc)
    return None


async def open_dm(
    credentials: Credentials, user_id: str
) -> tuple[str | None, str | None]:
    """``(channel, error)``: the DM channel with this person, opened if it was not."""
    try:
        client = await build_slack_client(credentials)
        response = await client.conversations_open(users=user_id)
    except slack_api_error() as exc:
        return None, _refused("conversations.open", exc)
    data = response.data if isinstance(response.data, dict) else {}
    channel = data.get("channel")
    if isinstance(channel, dict) and channel.get("id"):
        return str(channel.get("id")), None
    return None, "unknown_error"
