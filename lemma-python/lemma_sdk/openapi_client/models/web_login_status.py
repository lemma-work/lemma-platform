from enum import Enum


class WebLoginStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DEAD = "DEAD"

    def __str__(self) -> str:
        return str(self.value)
