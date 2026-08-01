from enum import Enum


class NotificationOrigin(str, Enum):
    AGENT_RUN = "AGENT_RUN"
    SCHEDULE_RUN = "SCHEDULE_RUN"
    WORKFLOW_RUN = "WORKFLOW_RUN"

    def __str__(self) -> str:
        return str(self.value)
