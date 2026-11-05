from enum import Enum


class RuntimeProfileAvailability(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    OFFLINE = "OFFLINE"
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"

    def __str__(self) -> str:
        return str(self.value)
