"""Matching logic for webhook events against stored schedules."""

from __future__ import annotations

from typing import Any, Dict, List

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WebhookScheduleMatcher:
    """Find matching webhook schedules for platform events."""

    def __init__(
        self,
        schedule_repository: ScheduleRepository | None = None,
    ):
        self.schedule_repository = schedule_repository
        if self.schedule_repository is None:
            raise ValueError("schedule_repository is required")

    async def match(self, source: str, metadata: Dict[str, Any]) -> List[ScheduleEntity]:
        logger.info("Matching webhook for source", source=source, metadata=metadata)
        if source == "composio":
            provider_id = metadata.get("provider_id")
            if not provider_id:
                logger.warning("Composio webhook missing provider_id in metadata")
                return []

            return await self.schedule_repository.find_by_config(
                schedule_type=ScheduleType.WEBHOOK,
                criteria={"provider_trigger_id": provider_id},
            )

        logger.info("No matching schedules found for %s webhook", source)
        return []
