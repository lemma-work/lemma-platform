from enum import Enum


class DeciderTestBodyVisibility(str, Enum):
    PERSONAL = "PERSONAL"
    POD = "POD"

    def __str__(self) -> str:
        return str(self.value)
