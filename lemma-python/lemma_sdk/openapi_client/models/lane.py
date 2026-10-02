from enum import Enum


class Lane(str, Enum):
    AMBIENT = "ambient"
    BULK = "bulk"
    INTERACTIVE = "interactive"

    def __str__(self) -> str:
        return str(self.value)
