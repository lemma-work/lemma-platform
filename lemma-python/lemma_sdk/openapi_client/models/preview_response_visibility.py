from enum import Enum


class PreviewResponseVisibility(str, Enum):
    LINK = "link"
    PRIVATE = "private"

    def __str__(self) -> str:
        return str(self.value)
