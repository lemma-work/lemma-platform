from enum import Enum


class PodConnectorStatus(str, Enum):
    NEEDS_ACCOUNT = "NEEDS_ACCOUNT"
    NEEDS_INSTALLATION = "NEEDS_INSTALLATION"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"

    def __str__(self) -> str:
        return str(self.value)
