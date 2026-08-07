from enum import Enum


class NotificationOriginKind(str, Enum):
    AGENT_RUN = "AGENT_RUN"
    API = "API"
    SCHEDULE = "SCHEDULE"
    WORKFLOW_FORM = "WORKFLOW_FORM"

    def __str__(self) -> str:
        return str(self.value)
