from enum import Enum


class CreatePreviewRequestVisibility(str, Enum):
    LINK = "link"
    PRIVATE = "private"

    def __str__(self) -> str:
        return str(self.value)
