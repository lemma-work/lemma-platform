from enum import Enum


class NotifyLevel(str, Enum):
    ALL = "ALL"
    MENTIONS = "MENTIONS"
    NONE = "NONE"

    def __str__(self) -> str:
        return str(self.value)
