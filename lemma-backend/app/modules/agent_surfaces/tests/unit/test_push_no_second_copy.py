"""``send_on_first_channel``: a push that may already be on one app is not
sent again on another.

A send that timed out after the request left -- or that delivered part of a
long message -- may be on their screen. Falling through to email then is a
second copy of the same push; the delivery's own outcome says what became of
it. A refusal that is the platform's answer still falls through.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.surface_config import SurfaceConfig
from app.modules.agent_surfaces.platforms.delivery import (
    RetryPolicy,
    classify_transport_failure,
    with_retry,
)
from app.modules.agent_surfaces.services.notification_delivery import (
    DeliveryChannel,
    Outbound,
    PlatformSent,
)
from app.modules.agent_surfaces.services.notification_dispatch import (
    send_on_first_channel,
)


def _surface(platform: SurfacePlatform) -> AgentSurfaceEntity:
    pod_id = uuid4()
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        agent_id=pod_id,
        name=platform.value.lower(),
        surface_type=platform,
        config=SurfaceConfig(),
        surface_identity_email="pod@ops.lemma.work",
    )


class _Egress:
    """The two platforms: Slack failing as ``slack``, and a mailbox."""

    def __init__(self, slack: Exception) -> None:
        self.slack = slack
        self.sent_on: list[str] = []

    async def conversation_for(self, channel, *, outbound):
        return uuid4()

    async def persist_outbound(self, **_kwargs) -> None:
        return None

    async def send(self, channel: DeliveryChannel, **_kwargs):
        self.sent_on.append(channel.platform.value)
        if channel.platform is SurfacePlatform.SLACK:

            async def post():
                raise self.slack

            await with_retry(
                post,
                policy=RetryPolicy(max_attempts=1),
                classify=classify_transport_failure,
            )
        return PlatformSent(external_message_id="email-1")


async def _push(slack: Exception) -> tuple[object, list[str]]:
    egress = _Egress(slack)
    slack_surface = _surface(SurfacePlatform.SLACK)
    channels = [
        DeliveryChannel(
            surface=slack_surface,
            source=SimpleNamespace(channel_id=uuid4()),
            external_user_id="U1",
        ),
        DeliveryChannel(
            surface=_surface(SurfacePlatform.RESEND), email_address="ada@example.com"
        ),
    ]

    async def commit() -> None:
        return None

    delivered = await send_on_first_channel(
        Outbound(
            id=uuid4(),
            pod_id=slack_surface.pod_id,
            recipient_user_id=uuid4(),
            title="t",
        ),
        channels=channels,
        message="What did you ship?",
        agent_name=None,
        uow=SimpleNamespace(commit=commit),  # type: ignore[arg-type]
        egress=egress,  # type: ignore[arg-type]
        email_budget=None,
    )
    return delivered, egress.sent_on


async def test_a_send_that_timed_out_after_leaving_is_not_emailed_too():
    delivered, sent_on = await _push(httpx.ReadTimeout("no answer"))

    assert sent_on == ["SLACK"]
    assert not delivered.sent and not delivered.retryable


async def test_a_send_that_never_left_falls_through_to_email():
    delivered, sent_on = await _push(httpx.ConnectError("refused"))

    assert sent_on == ["SLACK", "RESEND"]
    assert delivered.sent and delivered.platform == "RESEND"
