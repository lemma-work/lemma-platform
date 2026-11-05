from enum import Enum


class LinkState(str, Enum):
    GONE = "GONE"
    OPENED = "OPENED"
    OTHER_ACCOUNT = "OTHER_ACCOUNT"
    WAITING = "WAITING"

    def __str__(self) -> str:
        return str(self.value)
