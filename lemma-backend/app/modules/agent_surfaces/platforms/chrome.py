"""The app's own furniture on a platform, as opposed to a conversation.

An App Home tab, a setup modal, a starter prompt, a thread title, the list of
channels a bot can be pointed at. None of it is delivery: nothing here reaches
an agent or carries an answer, and a platform that has none of it is not missing
a feature.

Split from the delivery port because mixing the two made every platform read as
half-implemented. Eighteen outbound verbs, six of which only Slack has, is not a
platform contract with gaps in it -- it is two contracts in one class, and the
smaller one is Slack-shaped.

The defaults stay no-ops rather than raising. Shared code reaches for these on
whatever adapter it has (``parse_channel_setup`` runs on every inbound webhook),
and the regression that taught us so was an ``AttributeError`` in a worker, not
a missing feature.
"""

from __future__ import annotations

from typing import Any

from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.models import SurfaceChannelInfo


class SurfaceChromeMixin:
    """Setup and platform furniture. Every method optional, none of it delivery."""

    platform: str

    async def open_channel_setup_modal(
        self,
        *,
        credentials: dict[str, Any],
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_name: str,
        surface_id: str | None = None,
    ) -> bool:
        """Ask, in-chat, which agent answers in a channel. Default: unsupported."""
        del credentials, trigger_id, channel_id, channel_label, agent_name
        del surface_id
        return False

    async def send_starter_prompt(
        self, *, credentials: dict[str, Any], user_id: str, prompt: str
    ) -> bool:
        """Offer an opening prompt to a new user. Default: unsupported."""
        del credentials, user_id, prompt
        return False

    async def publish_home_view(
        self,
        *,
        credentials: dict[str, Any],
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
        """Render the app's home tab. Default: the platform has no home tab."""
        del credentials, user_id, pod_name, agent_name, channel_routes
        del agents, apps, workspace_url, logo_url, surface_choices, access_message
        return False

    async def send_channel_setup_prompt(
        self,
        *,
        credentials: dict[str, Any],
        channel_id: str,
        user_id: str,
        channel_name: str | None = None,
        confirmed_agent: str | None = None,
        surface_choices: list[tuple[str, str]] | None = None,
        configuration_error: str | None = None,
    ) -> bool:
        """Offer to configure a freshly joined channel. Default: unsupported."""
        del (
            credentials,
            channel_id,
            user_id,
            channel_name,
            confirmed_agent,
            surface_choices,
            configuration_error,
        )
        return False

    async def set_thread_title(
        self,
        *,
        credentials: dict[str, Any],
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        """Name the conversation thread on the platform. Default: unsupported."""
        del credentials, event, title
        return False

    async def parse_channel_setup(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Parse a payload belonging to the in-chat set-up flow. Default: the
        platform drives set-up from the web UI only → None, so the caller keeps
        treating the payload as an ordinary message."""
        del payload, headers
        return None

    async def channel_name(
        self, *, credentials: dict[str, Any], channel_id: str
    ) -> str | None:
        """Human-readable name for a channel. Default: unknown."""
        del credentials, channel_id
        return None

    async def list_channels(
        self, *, credentials: dict[str, Any]
    ) -> list[SurfaceChannelInfo]:
        """List channels/groups the bot can be configured in.

        Default: platform has no enumerable channels (DMs/groups the bot is
        added to but cannot list, e.g. Telegram/WhatsApp/email).
        """
        del credentials
        return []

    async def parse_inbound_lifecycle(
        self, payload: dict[str, Any], headers: dict[str, str] | None = None
    ):
        """Parse an event about the app itself. Default: not a lifecycle event."""
        del payload, headers
        return

    # The in-chat set-up flow. Only Slack drives configuration from inside the
    # chat app today, but `SurfaceConfigurationMixin` calls all of these on
    # whichever adapter the inbound webhook resolved to — so they are part of
    # the adapter contract, not Slack's private surface. Declared here with
    # inert defaults: a platform that cannot configure itself in-chat answers
    # "not mine" to the parse and "didn't do it" to the rest.
