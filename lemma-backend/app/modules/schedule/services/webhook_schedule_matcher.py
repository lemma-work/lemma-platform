"""Matching logic for webhook events against stored schedules."""

from __future__ import annotations

from typing import Any, Dict, List

from app.modules.schedule.domain.schedule import (
    PROVISIONED_CONFIG_KEYS,
    ScheduleEntity,
    ScheduleType,
)
from app.modules.schedule.contracts.webhook_source import WebhookPayload
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _provisioned_only(
    schedules: List[ScheduleEntity], criteria: Dict[str, Any]
) -> List[ScheduleEntity]:
    """A routing key only provisioning writes matches only schedules that were
    provisioned -- those bound to an account. Create and update already drop
    such keys from what an author sends; this holds for any row that got one
    another way, so a typed-in trigger or installation id never routes another
    tenant's events into it."""
    if not PROVISIONED_CONFIG_KEYS & set(criteria):
        return schedules
    return [schedule for schedule in schedules if schedule.account_id is not None]


class WebhookScheduleMatcher:
    """Find matching webhook schedules for platform events."""

    def __init__(
        self,
        schedule_repository: ScheduleRepository | None = None,
    ):
        self.schedule_repository = schedule_repository
        if self.schedule_repository is None:
            raise ValueError("schedule_repository is required")

    async def match(
        self, source: str, metadata: Dict[str, Any]
    ) -> List[ScheduleEntity]:
        if source == "composio":
            provider_id = metadata.get("provider_id")
            if not provider_id:
                logger.debug(
                    "schedule.webhook_schedule_matcher.composio_webhook_missing_provider_id.diagnostic"
                )
                return []

            criteria = {"provider_trigger_id": provider_id}
            return _provisioned_only(
                await self.schedule_repository.find_by_config(
                    schedule_type=ScheduleType.WEBHOOK, criteria=criteria
                ),
                criteria,
            )

        return []

    async def match_criteria(self, criteria: WebhookPayload) -> List[ScheduleEntity]:
        """Schedules whose stored config contains `criteria`.

        For a source plugin that states its own routing key. `match` above keeps
        deriving one per source for the sources that predate the plugins.

        Note the direction: containment is `config @> criteria`, so a schedule
        may declare *more* than the key and still match. Narrowing a schedule to
        one repository or a few actions is therefore a second pass over what
        this returns, not something expressible here.
        """
        if not criteria:
            return []
        return _provisioned_only(
            await self.schedule_repository.find_by_config(
                schedule_type=ScheduleType.WEBHOOK,
                criteria=criteria,
            ),
            criteria,
        )
