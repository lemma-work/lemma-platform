"""Say one plain sentence to someone, through the one outbound seam.

Onboarding prompts, the fallback reply to a stranger and the answers to Telegram
commands each used to call ``adapter.send_message`` directly, so each got its own
error handling and none reported how the message landed. They go through
``adapter.deliver`` now, like everything else a person reads.

Retries live in one place. The adapter retries a transient failure (rate limit,
5xx, timeout) up to ``MAX_DELIVERY_ATTEMPTS`` times with backoff; if it still
cannot reach the person, ``deliver`` raises ``AgentSurfacePlatformError``, which
the inbox treats as terminal. So the total for one message is the adapter's
attempts and no more: a job or an inbox retrying on top of that would multiply
them against an API that is already failing.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.envelope import DeliveryReceipt, SurfaceEnvelope


async def reply_text(
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: Mapping[str, object],
    event: ParsedInboundSurfaceEvent,
    message: str,
    metadata: Mapping[str, object] | None = None,
) -> DeliveryReceipt:
    """Deliver ``message`` and raise if nothing reached the person.

    A caller that must not fail because a courtesy message did not land catches
    ``AgentSurfaceError`` itself, under its own named log event; one that must
    know lets it propagate.
    """
    return await adapter.deliver(
        credentials=dict(credentials),
        event=event,
        envelope=SurfaceEnvelope(text=message),
        metadata=dict(metadata) if metadata is not None else None,
    )
