from enum import Enum


class TabCommandCommand(str, Enum):
    ACTIVATE = "activate"
    CLOSE = "close"

    def __str__(self) -> str:
        return str(self.value)
