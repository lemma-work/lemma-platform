"""Keeping a webhook schedule's remote subscription in step with its config.

A connector trigger's config is the subscription's filter: Composio is told
which repository, label or inbox to watch when the subscription is made. An
update that changed the config and left the subscription alone kept the
schedule firing on the old filter. Two keys in the stored config are also not
the author's -- `provider_trigger_id`, and the routing key a local binder such
as GitHub's writes -- and an author's update never carries them, so writing
the config back as sent dropped them: the schedule stopped routing, and delete
no longer knew what to unsubscribe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.log.log import get_logger
from app.modules.schedule.domain.errors import (
    ScheduleInfrastructureError,
    ScheduleValidationError,
)
from app.modules.schedule.domain.interfaces import (
    ExternalScheduleWriter,
    ScheduleConfig,
)
from app.modules.schedule.domain.schedule import ScheduleEntity

logger = get_logger(__name__)

PROVIDER_TRIGGER_ID = "provider_trigger_id"


class _RowWriter(Protocol):
    async def update(
        self, schedule_id: UUID, **kwargs: object
    ) -> ScheduleEntity | None: ...


class _AfterCommit(Protocol):
    @property
    def session(self) -> object: ...

    def after_commit(self, callback: Callable[[], Awaitable[object]]) -> None: ...


@dataclass(frozen=True)
class SubscriptionSwap:
    """The subscription a schedule had, and the one made for its new config."""

    old: ScheduleEntity
    new: ScheduleEntity


async def resubscribe_for_new_config(
    existing: ScheduleEntity,
    update_data: dict[str, object],
    writer: ExternalScheduleWriter,
) -> SubscriptionSwap | None:
    """Rewrite ``update_data["config"]`` so it still routes; make a new
    subscription when the filter changed.

    Returns the swap when a remote subscription was made, so the caller can
    drop whichever one loses. ``None`` means nothing remote changed.
    """
    sent = update_data.get("config")
    if not isinstance(sent, dict) or not existing.listens_through_account:
        return None
    authored: ScheduleConfig = {
        key: value for key, value in sent.items() if key != PROVIDER_TRIGGER_ID
    }
    previous = {
        key: value
        for key, value in existing.config.items()
        if key != PROVIDER_TRIGGER_ID
    }
    if authored == previous:
        update_data["config"] = dict(existing.config)
        return None
    candidate = existing.model_copy(update={"config": authored})
    provisioned = await writer.create_provider_trigger(candidate)
    config = dict(authored)
    provisioned.apply_to(config)
    update_data["config"] = config
    if not provisioned.provider_trigger_id:
        return None
    return SubscriptionSwap(
        old=existing, new=candidate.model_copy(update={"config": config})
    )


async def drop_subscription(
    schedule: ScheduleEntity, writer: ExternalScheduleWriter
) -> None:
    """Best effort. A subscription left behind delivers events that no schedule
    matches, because routing is by the provider id the row no longer holds."""
    try:
        await writer.delete_provider_trigger(schedule)
    except ScheduleInfrastructureError, ScheduleValidationError:
        logger.warning(
            "schedule.trigger_resubscription.drop.degraded",
            schedule_id=str(schedule.id),
            exc_info=True,
        )


class _Writes(Protocol):
    """The three things of the schedule service an update touches."""

    @property
    def schedule_repository(self) -> _RowWriter: ...

    @property
    def external_schedule_writer(self) -> ExternalScheduleWriter: ...

    @property
    def uow(self) -> _AfterCommit: ...


async def update_schedule_resubscribing(
    existing: ScheduleEntity, update_data: dict[str, object], service: _Writes
) -> ScheduleEntity | None:
    """Write the row, swapping its remote subscription if the filter changed.

    The new subscription is made before the row is written, so a provider that
    refuses the new config refuses the update and the old one keeps firing. The
    old one is dropped only after the commit: dropping it first and then failing
    to commit would leave the row pointing at a subscription that is gone. Both
    provider calls hand the request's pooled connection back while they run.
    """
    writer, uow = service.external_schedule_writer, service.uow
    async with connection_released(uow.session):
        swap = await resubscribe_for_new_config(existing, update_data, writer)
    updated: ScheduleEntity | None = None
    try:
        updated = await service.schedule_repository.update(existing.id, **update_data)
    finally:
        if swap is not None and updated is None:
            async with connection_released(uow.session):
                await drop_subscription(swap.new, writer)
    if swap is not None and updated is not None:
        old = swap.old
        uow.after_commit(lambda: drop_subscription(old, writer))
    return updated
