from enum import Enum


class DecideRowsBodyVisibility(str, Enum):
    PERSONAL = "PERSONAL"
    POD = "POD"

    def __str__(self) -> str:
        return str(self.value)
