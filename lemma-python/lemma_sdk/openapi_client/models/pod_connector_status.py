from enum import Enum


class PodConnectorStatus(str, Enum):
    NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"

    def __str__(self) -> str:
        return str(self.value)
