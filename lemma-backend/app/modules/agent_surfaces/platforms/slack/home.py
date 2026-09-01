"""Slack's configuration surfaces: App Home, modals, and setup prompts.

Split out of :mod:`service` because these answer a different question. The
service delivers an agent's replies; this is where a *person* sets Lemma up
without leaving Slack — the ephemeral that follows an invite, the modals behind
it, and the Home tab.
"""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent

from app.modules.agent_surfaces.platforms.slack.blocks import (
    channel_setup_confirmation_blocks,
    channel_setup_modal,
    channel_setup_prompt_blocks,
    truncate_slack_text as _truncate_slack_text,
)
from app.modules.agent_surfaces.platforms.slack.home_blocks import (
    app_home_view,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_scopes,
)

logger = get_logger(__name__)


class SlackHomeSurface:
    """Everything a person interacts with to configure Lemma inside Slack."""

    def __init__(self, *, credentials: dict[str, Any]) -> None:
        self.credentials = credentials

    async def send_channel_setup_prompt(
        self,
        *,
        channel_id: str,
        user_id: str,
        channel_name: str | None = None,
        confirmed_agent: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        configuration_error: str | None = None,
    ) -> bool:
        """Ask the person who just added Lemma who should answer here.

        Ephemeral, so an unconfigured channel never gets bot noise in front of
        everyone. Returns False when there is nobody to ask (Slack records no
        inviter when the bot joins itself via ``chat:write.public``).
        """
        token = slack_access_token(self.credentials)
        if not token or not channel_id or not user_id:
            return False
        try:
            client = await build_slack_client(self.credentials)
            await client.chat_postEphemeral(
                channel=str(channel_id),
                user=str(user_id),
                text=(
                    configuration_error
                    or (
                        f"{confirmed_agent} now answers in this channel."
                        if confirmed_agent
                        else "Choose which agent answers in this channel."
                    )
                ),
                blocks=(
                    [{"type": "markdown", "text": configuration_error}]
                    if configuration_error
                    else channel_setup_confirmation_blocks(
                        channel_name=channel_name, agent_label=confirmed_agent
                    )
                    if confirmed_agent
                    else channel_setup_prompt_blocks(
                        channel_id=str(channel_id),
                        channel_name=channel_name,
                        surface_choices=surface_choices,
                    )
                ),
            )
            return True
        except SlackApiError:
            logger.debug("agent_surfaces.service.slack_channel_setup_prompt.diagnostic")
            return False

    async def open_channel_setup_modal(
        self,
        *,
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_name: str,
        surface_id: str | None = None,
    ) -> bool:
        """Open the "who answers here?" modal.

        Must be called within ~3 seconds of the button tap: Slack expires the
        trigger_id, and there is no way to reopen it without another tap.
        """
        token = slack_access_token(self.credentials)
        if not token or not trigger_id:
            return False
        try:
            client = await build_slack_client(self.credentials)
            await client.views_open(
                trigger_id=trigger_id,
                view=channel_setup_modal(
                    channel_id=channel_id,
                    channel_label=channel_label,
                    agent_name=agent_name,
                    surface_id=surface_id,
                ),
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                "agent_surfaces.service.slack_open_setup_modal.diagnostic",
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def send_starter_prompt(self, *, user_id: str, prompt: str) -> bool:
        """Open the DM and drop the starter question in as an ephemeral nudge.

        The point is that a first-time viewer gets a real answer without having
        to think of a question. Slack has no way to *speak as the user*, so this
        opens their DM and shows the prompt to copy — honest about what it is,
        rather than faking a message from them.
        """
        token = slack_access_token(self.credentials)
        if not token or not user_id or not prompt:
            return False
        client = await build_slack_client(self.credentials)
        try:
            opened = await client.conversations_open(users=str(user_id))
            channel = ((opened.get("channel") or {}).get("id")) or ""
            if not channel:
                return False
            await client.chat_postEphemeral(
                channel=str(channel),
                user=str(user_id),
                text=prompt,
                blocks=[
                    {
                        "type": "markdown",
                        "text": f"Try asking me:\n\n> {prompt}",
                    }
                ],
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                "agent_surfaces.service.slack_starter_prompt.diagnostic",
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def publish_home_view(
        self,
        *,
        user_id: str,
        pod_name: str | None,
        agent_name: str,
        channel_routes: list,
        agents: list | None = None,
        apps: list | None = None,
        workspace_url: str | None = None,
        logo_url: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        access_message: str | None = None,
    ) -> bool:
        """Publish the Home tab for one person."""
        token = slack_access_token(self.credentials)
        if not token or not user_id:
            return False
        try:
            client = await build_slack_client(self.credentials)
            await client.views_publish(
                user_id=str(user_id),
                view=app_home_view(
                    pod_name=pod_name,
                    agent_name=agent_name,
                    channel_routes=channel_routes,
                    agents=agents,
                    apps=apps,
                    workspace_url=workspace_url,
                    logo_url=logo_url,
                    surface_choices=surface_choices,
                    access_message=access_message,
                ),
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                "agent_surfaces.service.slack_publish_home_view.diagnostic",
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def channel_name(self, channel_id: str) -> str | None:
        """Best-effort channel name, so prompts can say #sales not "this channel"."""
        token = slack_access_token(self.credentials)
        if not token or not channel_id:
            return None
        try:
            client = await build_slack_client(self.credentials)
            response = await client.conversations_info(channel=str(channel_id))
            name = ((response.get("channel") or {}).get("name") or "").strip()
            return name or None
        except SlackApiError:
            return None

    async def set_thread_title(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        """Name the agent thread, so Slack's own DM history is navigable.

        Only meaningful in a DM under the agent messaging experience. Entirely
        best-effort: a workspace on an older install has no ``assistant:write``
        and simply keeps Slack's default thread naming.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        clean_title = _truncate_slack_text(str(title or "").strip(), 250)
        if not token or not channel or not thread_ts or not clean_title:
            return False
        if not event.is_dm or "assistant:write" not in slack_scopes(self.credentials):
            return False
        try:
            client = await build_slack_client(self.credentials)
            await client.assistant_threads_setTitle(
                channel_id=str(channel),
                thread_ts=str(thread_ts),
                title=clean_title,
            )
            return True
        except SlackApiError:
            logger.debug("agent_surfaces.service.slack_set_thread_title.diagnostic")
            return False

    async def set_suggested_prompts(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        prompts: list[tuple[str, str]],
        title: str | None = None,
    ) -> bool:
        """Offer tappable openers instead of an empty box.

        ``prompts`` is ``(title, message)`` pairs — the title is the chip label,
        the message is what gets sent when it is tapped. Slack accepts at most
        four.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel or not prompts:
            return False
        if not event.is_dm or "assistant:write" not in slack_scopes(self.credentials):
            return False
        payload_prompts = [
            {
                "title": _truncate_slack_text(str(prompt_title).strip(), 100),
                "message": str(prompt_message).strip(),
            }
            for prompt_title, prompt_message in prompts[:4]
            if str(prompt_title).strip() and str(prompt_message).strip()
        ]
        if not payload_prompts:
            return False
        kwargs: dict[str, Any] = {
            "channel_id": str(channel),
            "prompts": payload_prompts,
        }
        # Optional since the agent messaging experience shipped; passing it
        # still scopes the prompts to one thread where the app is on the older
        # assistant view.
        thread_ts = event.reply_target.get("thread_ts")
        if thread_ts:
            kwargs["thread_ts"] = str(thread_ts)
        if title:
            kwargs["title"] = _truncate_slack_text(str(title).strip(), 100)
        try:
            client = await build_slack_client(self.credentials)
            await client.assistant_threads_setSuggestedPrompts(**kwargs)
            return True
        except SlackApiError:
            logger.debug(
                "agent_surfaces.service.slack_set_suggested_prompts.diagnostic"
            )
            return False
