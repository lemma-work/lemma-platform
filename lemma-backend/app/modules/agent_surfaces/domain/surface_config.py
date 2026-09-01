"""User-editable surface configuration.

Split out of :mod:`entities` because these are the shapes a *person* edits —
who answers where, which senders are allowed, which app is pinned — as opposed
to the inbound/outbound event shapes the rest of that module describes.
"""

from __future__ import annotations


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
            normalized in self.allowed_email_addresses or domain in self.allowed_domains
        )


class SurfaceChannelRoute(BaseModel):
    """One channel this surface's agent may be spoken to in.

    An allow-list entry, not a routing rule. A surface belongs to exactly one
    agent, so a channel says *where* that agent can be reached, never *who*
    answers — the answer is always ``surface.agent_id``.

    It used to name an agent, because one bot could serve several. That needed
    a third state on top ("the pod assistant answers here") which no name could
    express, since the assistant had no row to be named by. Both are gone: one
    bot, one agent, and a channel is a place rather than a choice.

    A route existing means the channel is allowed; remove it to stop answering
    there.
    """

    channel_id: str | None = None
    channel_name: str | None = None

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

    ``app_name`` mirrors :class:`SurfaceTelegramConfig` — the pod app to offer
    from the channel's bookmark bar and App Home, Slack's nearest equivalent to
    Telegram's chat menu button.

    This used to carry the per-person "who answers my DMs?" map, and a
    ``dedicated_to_agent`` flag saying whether to honour it. Both existed
    because one Slack app could serve several agents and ``surface.agent_id``
    could not say which of them it really was. One app is one agent now, so the
    question has one answer and neither field has anything left to decide.
    """

    app_name: str | None = None


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
