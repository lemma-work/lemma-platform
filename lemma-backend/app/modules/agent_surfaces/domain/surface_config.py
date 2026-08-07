"""User-editable surface configuration.

Split out of :mod:`entities` because these are the shapes a *person* edits —
who answers where, which senders are allowed, which app is pinned — as opposed
to the inbound/outbound event shapes the rest of that module describes.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class SurfaceIdentityPolicy(BaseModel):
    """Restricts which resolved senders may use the surface (empty = everyone)."""

    allowed_domains: list[str] = Field(default_factory=list)
    allowed_email_addresses: list[str] = Field(default_factory=list)

    @field_validator("allowed_domains", "allowed_email_addresses")
    @classmethod
    def _normalize(cls, values: list[str]) -> list[str]:
        return [value.strip().lower() for value in values if str(value).strip()]

    def allows_email(self, email: str | None) -> bool:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return True
        if not self.allowed_email_addresses and not self.allowed_domains:
            return True
        domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else ""
        return (
            normalized in self.allowed_email_addresses
            or domain in self.allowed_domains
        )


class SurfaceChannelRoute(BaseModel):
    """Routes one platform channel to an agent (by pod-unique agent name).

    Three states, not two — conflating the last two silently sends "the pod
    assistant" to whichever agent the surface happens to default to:

    * ``agent_name`` set — that named agent answers.
    * ``use_pod_assistant`` — the pod's own assistant answers, which is a
      conversation with *no* agent at all, not a fallback.
    * neither — unconfigured, so the surface default answers.

    A route existing means it is active; remove it to stop routing the channel.
    """

    channel_id: str | None = None
    channel_name: str | None = None
    agent_name: str | None = None
    use_pod_assistant: bool = False

    def matches(self, *, channel_id: str, channel_name: str) -> bool:
        route_channel_id = str(self.channel_id or "").strip()
        if channel_id and route_channel_id and route_channel_id == channel_id:
            return True
        route_channel_name = str(self.channel_name or "").strip().lower()
        return bool(
            channel_name and route_channel_name and route_channel_name == channel_name
        )


class SurfaceSendPolicy(BaseModel):
    """Controls the surface's own proactive-send tool (``surface.send``).

    Deliberately NOT a gate on reaching other pod members. That capability is
    granted per *agent*, by giving it the ``MESSAGING`` toolset — an agent that
    holds the tool can contact colleagues, and one that does not, cannot. Putting
    a second gate on the surface meant an editor had to flip a setting on a bot
    before a grant they had already made would take effect, which is a rule
    nobody would guess and the wrong place to express it.

    Reaching the person a run *belongs to* needs no permission at all: the run
    already carries their delegated authority.
    """

    # Expose the current-user ``surface_send_message`` tool to the agent.
    allow_send: bool = False


class SurfaceTelegramConfig(BaseModel):
    """Telegram-only presentation settings for a surface."""

    app_name: str | None = None


class SurfaceSlackConfig(BaseModel):
    """Slack-only settings for a surface.

    ``dm_agent_by_user`` is the per-person answer to "who am I talking to?" —
    Slack external user id to pod-unique agent name. Slack DMs otherwise route
    to one agent for the whole workspace (``surface.agent_id``), which is the
    single hard limit the platform used to impose on us. A person picks their
    own agent; anyone who has not picked keeps the surface default, so this is
    additive and needs no migration.

    ``app_name`` mirrors :class:`SurfaceTelegramConfig` — the pod app to offer
    from the channel's bookmark bar and App Home, Slack's nearest equivalent to
    Telegram's chat menu button.
    """

    # Stored when someone explicitly picks the pod assistant. Deleting the
    # entry instead would make "chose the pod assistant" identical to "never
    # chose", so their pick would silently read as the surface default.
    POD_ASSISTANT: ClassVar[str] = "__pod_assistant__"

    dm_agent_by_user: dict[str, str] = Field(default_factory=dict)
    app_name: str | None = None

    def choice_for_user(self, external_user_id: str | None) -> str | None:
        """Whatever this person picked — an agent name, the pod-assistant
        sentinel, or None for "never picked"."""
        if not external_user_id:
            return None
        return self.dm_agent_by_user.get(str(external_user_id)) or None

    def agent_for_user(self, external_user_id: str | None) -> str | None:
        """The named agent this person chose. None for the pod assistant *or*
        for no choice — callers that must tell those apart use
        :meth:`chose_pod_assistant`."""
        choice = self.choice_for_user(external_user_id)
        return None if choice == self.POD_ASSISTANT else choice

    def chose_pod_assistant(self, external_user_id: str | None) -> bool:
        return self.choice_for_user(external_user_id) == self.POD_ASSISTANT


class SurfaceConfig(BaseModel):
    """User-editable surface behavior. Exactly what the API accepts and returns.

    Derived/identity data (workspace ids, secrets, schedule links) lives in
    dedicated entity fields, never in here.
    """

    dm_conversation_reset_after_hours: int = 24
    identity: SurfaceIdentityPolicy = Field(default_factory=SurfaceIdentityPolicy)
    channels: list[SurfaceChannelRoute] = Field(default_factory=list)
    send_policy: SurfaceSendPolicy = Field(default_factory=SurfaceSendPolicy)
    telegram: SurfaceTelegramConfig = Field(default_factory=SurfaceTelegramConfig)
    slack: SurfaceSlackConfig = Field(default_factory=SurfaceSlackConfig)

