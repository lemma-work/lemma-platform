"""Schedule adapters used by surface lifecycle management."""

from app.modules.schedule.api.dependencies import get_schedule_service
from app.modules.schedule.services.schedule_service import ScheduleService

__all__ = ["ScheduleService", "get_schedule_service"]
