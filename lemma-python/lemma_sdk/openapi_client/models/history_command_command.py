from enum import Enum


class HistoryCommandCommand(str, Enum):
    BACK = "back"
    FORWARD = "forward"
    RELOAD = "reload"

    def __str__(self) -> str:
        return str(self.value)
