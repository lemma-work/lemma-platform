from enum import Enum


class PendingInteractionKind(str, Enum):
    APPROVAL = "APPROVAL"
    PERMISSION = "PERMISSION"
    QUESTION = "QUESTION"

    def __str__(self) -> str:
        return str(self.value)
