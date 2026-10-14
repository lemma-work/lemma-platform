"""The Slack calls the manager makes: views, ephemerals, and moving bots between
channels.

Each takes the credentials of the bot making the call -- the manager's own for
its Home and its prompts, a teammate's for that teammate joining a channel --
and answers with Slack's error code rather than raising, because every caller
here has something better to say to a person than a stack trace.
"""

from __future__ import annotations

import asyncio

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


async def publish_home(
    credentials: Credentials, *, user_id: str, view: Mapping[str, object]
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.views_publish(user_id=user_id, view=dict(view))
    except slack_api_error() as exc:
        logger.info(
            "agent_surfaces.slack_manager.home_publish_failed", error=_error(exc)
        )
        return _error(exc)
    return None


async def open_modal(
    credentials: Credentials, *, trigger_id: str, view: Mapping[str, object]
) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.views_open(trigger_id=trigger_id, view=dict(view))
    except slack_api_error() as exc:
        logger.info("agent_surfaces.slack_manager.modal_open_failed", error=_error(exc))
        return _error(exc)
    return None


async def update_view(
    credentials: Credentials, *, view_id: str, view: Mapping[str, object]
) -> str | None:
    """Replace an open modal: from "making the app" to "allow it", say."""
    try:
        client = await build_slack_client(credentials)
        await client.views_update(view_id=view_id, view=dict(view))
    except slack_api_error() as exc:
        logger.info(
            "agent_surfaces.slack_manager.view_update_failed", error=_error(exc)
        )
        return _error(exc)
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
        logger.info("agent_surfaces.slack_manager.ephemeral_failed", error=_error(exc))
        return _error(exc)
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
        logger.info("agent_surfaces.slack_manager.message_failed", error=_error(exc))
        return _error(exc)
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
        logger.info("agent_surfaces.slack_manager.respond_failed")


async def is_private_channel(credentials: Credentials, channel: str) -> bool | None:
    """True for a private channel, None when Slack would not say."""
    try:
        client = await build_slack_client(credentials)
        response = await client.conversations_info(channel=channel)
    except slack_api_error():
        return None
    data = response.data if isinstance(response.data, dict) else {}
    info = data.get("channel")
    return bool(info.get("is_private")) if isinstance(info, dict) else None


async def join_channel(credentials: Credentials, channel: str) -> str | None:
    """A bot joining a public channel by itself (needs ``channels:join``)."""
    try:
        client = await build_slack_client(credentials)
        await client.conversations_join(channel=channel)
    except slack_api_error() as exc:
        return _error(exc)
    return None


async def invite(
    credentials: Credentials, *, channel: str, user_ids: Sequence[str]
) -> str | None:
    """Invite users -- another app's bot included -- into a channel the caller is in."""
    try:
        client = await build_slack_client(credentials)
        await client.conversations_invite(channel=channel, users=",".join(user_ids))
    except slack_api_error() as exc:
        if _error(exc) == "already_in_channel":
            return None
        return _error(exc)
    return None


async def leave_channel(credentials: Credentials, channel: str) -> str | None:
    try:
        client = await build_slack_client(credentials)
        await client.conversations_leave(channel=channel)
    except slack_api_error() as exc:
        return _error(exc)
    return None


async def open_dm(credentials: Credentials, user_id: str) -> str | None:
    """The DM channel with this person, opened if it was not."""
    try:
        client = await build_slack_client(credentials)
        response = await client.conversations_open(users=user_id)
    except slack_api_error():
        return None
    data = response.data if isinstance(response.data, dict) else {}
    channel = data.get("channel")
    return (
        str(channel.get("id"))
        if isinstance(channel, dict) and channel.get("id")
        else None
    )


async def channel_member_ids(
    credentials: Credentials, channel: str, *, limit: int
) -> list[str] | None:
    """Who is in the channel, by Slack id, up to ``limit``. None when Slack refused:
    "nobody here" and "Slack would not say" are different answers."""
    try:
        client = await build_slack_client(credentials)
        response = await client.conversations_members(channel=channel, limit=limit)
    except slack_api_error() as exc:
        logger.info("agent_surfaces.slack_manager.members_refused", error=_error(exc))
        return None
    data = response.data if isinstance(response.data, dict) else {}
    members = data.get("members")
    return (
        [str(member) for member in members[:limit]]
        if isinstance(members, list)
        else None
    )


async def user_profiles(
    credentials: Credentials, user_ids: Sequence[str], *, at_once: int = 8
) -> dict[str, dict[str, object]]:
    """``users.info`` for each of these people, a few at a time on one client.

    A person Slack would not describe is absent from the answer, so a caller can
    tell "unknown" from a profile that says something.
    """
    client = await build_slack_client(credentials)
    gate = asyncio.Semaphore(at_once)

    async def one(user_id: str) -> tuple[str, dict[str, object]]:
        async with gate:
            try:
                response = await client.users_info(user=user_id)
            except slack_api_error():
                return user_id, {}
        data = response.data if isinstance(response.data, dict) else {}
        user = data.get("user")
        return user_id, user if isinstance(user, dict) else {}

    answers = await asyncio.gather(*(one(user_id) for user_id in user_ids))
    return {user_id: profile for user_id, profile in answers if profile}


async def user_profile(credentials: Credentials, user_id: str) -> dict[str, object]:
    """Slack's user record (``users.info``), or nothing when it won't say."""
    try:
        client = await build_slack_client(credentials)
        response = await client.users_info(user=user_id)
    except slack_api_error():
        return {}
    data = response.data if isinstance(response.data, dict) else {}
    user = data.get("user")
    return user if isinstance(user, dict) else {}
