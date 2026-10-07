from enum import Enum


class MakeDecisionRequestPriority(str, Enum):
    BACKGROUND = "background"
    INTERACTIVE = "interactive"

    def __str__(self) -> str:
        return str(self.value)
