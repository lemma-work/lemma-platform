"""A channel line's first email to an address nobody has written from yet.

A member's first word to a contact by email, sent from the pod's mailbox: a
thread seeded so the reply comes back to the line's place
(``cold_email_thread``), under the delivery's key so a retry is the same
email. A later line before they reply goes into the same thread, under the
same subject, rather than opening another each time.
"""

from __future__ import annotations


from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import IDEMPOTENCY_KEY
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity
from app.modules.agent_surfaces.platforms.delivery import track_sends
from app.modules.agent_surfaces.services.cold_email_thread import (
    cold_thread_seed_id,
)
from app.modules.agent_surfaces.services.delivery_outcome import Outcome, of_send
from app.modules.agent_surfaces.services.group_names import sender_name_for
from app.modules.agent_surfaces.services.member_pushes import title_of
from app.modules.agent_surfaces.services.notification_channels import can_cold_open
from app.modules.agent_surfaces.services.run_answer_sends import (
    EgressFor,
    delivery_key,
    send_errors,
)
from app.modules.channels.contracts import Channel, ChannelMessage, Delivery, Source
from app.modules.channels.contracts.deliveries import delivery_noted
from app.modules.channels.contracts.places import addressed_at

logger = get_logger(__name__)


async def cold_open_surface(
    uow_factory: UnitOfWorkFactory, egress_for: EgressFor, source: Source
) -> AgentSurfaceEntity | None:
    """The mailbox that can write first to this place, or ``None``."""
    if source.surface_id is None or not source.external_user_id:
        return None
    async with uow_factory() as uow:
        surface = await egress_for(uow).delivery.surface_repository.get(
            source.surface_id
        )
    if surface is None or not can_cold_open(surface):
        return None
    return surface


async def send_cold_open(
    uow_factory: UnitOfWorkFactory,
    egress_for: EgressFor,
    *,
    delivery: Delivery,
    line: ChannelMessage,
    channel: Channel,
    source: Source,
    surface: AgentSurfaceEntity,
    text: str,
    metadata: dict[str, object],
) -> Outcome:
    """Send it, and remember the thread it opened as the place's address."""
    platform = source.platform
    recipient = source.external_user_id
    assert recipient is not None  # cold_open_surface
    seed, subject = thread_so_far(source)
    thread = None
    raised: BaseException | None = None
    with track_sends() as sends:
        async with uow_factory() as uow:
            egress = egress_for(uow)
            metadata = {
                **metadata,
                IDEMPOTENCY_KEY: delivery_key(delivery),
                "email_sender_name": await sender_name_for(
                    uow,
                    is_pod_default=True,
                    agent_name=None,
                    pod_id=surface.pod_id,
                ),
            }
            try:
                thread = await egress.open_cold_email_thread(
                    surface=surface,
                    recipient_email=recipient,
                    subject=subject or channel.title or title_of(line.text),
                    message=text,
                    # By the line: a retry of it seeds the same thread.
                    thread_seed_id=seed
                    or cold_thread_seed_id(notification_id=line.id, surface=surface),
                    metadata=metadata,
                )
            except send_errors() as exc:
                logger.warning(
                    "agent_surfaces.channel_deliveries.send_failed.degraded",
                    platform=platform,
                    error_type=type(exc).__name__,
                )
                raised = exc
            if thread is not None:
                if thread.external_message_id:
                    await delivery_noted(
                        uow,
                        delivery.id,
                        external_message_id=thread.external_message_id,
                    )
                await addressed_at(uow, source.id, thread.last_event)
                await uow.commit()
    if thread is None:
        return of_send(
            None,
            sends,
            platform=platform,
            raised=raised,
            nothing_sent="There was nothing to send once it was cleaned up.",
        )
    return Outcome.delivered(platform, external_message_id=thread.external_message_id)


def thread_so_far(source: Source) -> tuple[str | None, str | None]:
    """The thread a first email to this place opened, and its subject, when
    one went before they replied: a follow-up goes into it rather than
    starting another."""
    ref = source.delivery_ref or {}
    seed = ref.get("external_thread_id")
    target = ref.get("reply_target")
    subject = target.get("subject") if isinstance(target, dict) else None
    return (
        seed if isinstance(seed, str) and seed else None,
        subject if isinstance(subject, str) and subject else None,
    )


__all__ = ["cold_open_surface", "send_cold_open", "thread_so_far"]
