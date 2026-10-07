from enum import Enum


class Aim(str, Enum):
    HIGHER = "higher"
    LOWER = "lower"

    def __str__(self) -> str:
        return str(self.value)
