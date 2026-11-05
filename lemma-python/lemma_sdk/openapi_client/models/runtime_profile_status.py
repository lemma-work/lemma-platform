from enum import Enum


class RuntimeProfileStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"

    def __str__(self) -> str:
        return str(self.value)
