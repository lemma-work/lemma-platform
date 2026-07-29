from enum import Enum


class AgentHostCommandKind(str, Enum):
    CANCEL_RUN = "CANCEL_RUN"
    CLOSE_SESSION = "CLOSE_SESSION"
    DRAIN = "DRAIN"
    REFRESH_HARNESS = "REFRESH_HARNESS"
    RESUME = "RESUME"
    ROTATE_DEVICE_KEY = "ROTATE_DEVICE_KEY"
    START_RUN = "START_RUN"

    def __str__(self) -> str:
        return str(self.value)
